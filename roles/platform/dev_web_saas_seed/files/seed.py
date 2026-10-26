#!/usr/bin/env python3
"""Reviewed Dev-only execution owner. Caller must authenticate target and authorization.

JSON request on stdin contains public identity only. Secrets are generated in memory
and sent to Kubernetes on stdin; never output kubectl errors or raw job logs.
"""
import base64
import hashlib
import json
import re
import secrets
import subprocess
import sys
import urllib.request
from urllib.parse import quote
from platform_scope import preflight
from contracts import ACCOUNTS_SHA, BILLING_SHA, SCHEMA_SHA, target, identity, verify_sources, grants

ACCOUNTS_IMAGE = 'ghcr.io/ai-workspace-services/accounts@sha256:6edd156ce6974dfd0cd7486beebbb446091e4019eacf3c272e05aba4a609737d'
PG_IMAGE = 'ghcr.io/ai-workspace-services/postgresql@sha256:c6ee94d4c91054128823e816e936e03702eee80fdcc04d3271a7e68167974ad2'
OWNER = 'dev-web-saas-seed-v1'


class Blocked(Exception):
    pass


class Owner:
    def __init__(self, request):
        self.r = request
        target(request['stage'], request['namespace'])
        self.ns = request['namespace']
        config = {**request, 'environment':request['stage'], 'mode':'prepare', 'node_name':'dev-k3s-v5'}
        preflight(config, self.k('get','nodes'), self.k('get','namespace','kube-system'),
                  self.k('-n','flux-system','get','gitrepository','platform-config'),
                  self.k('-n','flux-system','get','kustomization','platform-root'))
        for key in ('candidate_sha',):
            if not re.fullmatch('[0-9a-f]{40}', request[key]):
                raise Blocked('invalid candidate')
        for key in ('instance_id', 'node_uid', 'cluster_uid'):
            if not isinstance(request[key], str) or not request[key]:
                raise Blocked('missing trusted target identity')
        # The parent caller must bind instance_id to CMDB before SSH. This checks
        # the independently supplied in-cluster identity again, not API credentials.
        if self.k('get', 'namespace', 'kube-system')['metadata']['uid'] != request['cluster_uid']:
            raise Blocked('cluster identity mismatch')
        nodes = self.k('get', 'nodes')['items']
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != request['node_uid']:
            raise Blocked('single-node identity mismatch')
        ns = self.k('get', 'namespace', self.ns)
        if ns['metadata'].get('labels', {}).get('environment') != request['stage']:
            raise Blocked('namespace stage mismatch')

    def raw(self, *args, data=None):
        p = subprocess.run(['kubectl', *args], input=data, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode:
            raise Blocked('Kubernetes operation failed (sensitive output suppressed)')
        return p.stdout

    def k(self, *args):
        return json.loads(self.raw(*args, '-o', 'json'))

    def apply(self, resource):
        self.raw('apply', '-f', '-', data=json.dumps(resource))

    def secret(self, name, fields):
        # Never rotate/adopt silently. Owner-labelled existing secrets are reused.
        entries = self.k('-n', self.ns, 'get', 'secrets')['items']
        existing = next((s for s in entries if s['metadata']['name'] == name), None)
        if existing:
            if existing['metadata'].get('labels', {}).get('seed-owner') != OWNER:
                raise Blocked('existing secret has unknown owner')
            values = {k: base64.b64decode(v).decode() for k, v in existing['data'].items()}
            if set(values) != set(fields):
                raise Blocked('existing secret field contract mismatch')
            return values
        self.apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
            'name': name, 'namespace': self.ns, 'labels': {'seed-owner': OWNER}},
            'type': 'Opaque', 'stringData': fields})
        return fields

    def psql(self, sql):
        # Fixed PG management Pod; stdin SQL never enters argv. Do not expose raw
        # server diagnostics, which can quote statements containing credentials.
        return self.raw('-n', self.ns, 'exec', '-i', 'web-saas-postgresql-0', '--',
                        'psql', '-XAtq', '-U', 'lane_admin', '-d', 'account',
                        '-v', 'ON_ERROR_STOP=1', data=sql)

    def job(self, purpose, image, command, env=None):
        name = 'dev-seed-' + purpose + '-' + secrets.token_hex(4)
        pod = {'automountServiceAccountToken': False, 'restartPolicy': 'Never',
               'imagePullSecrets': [{'name': 'web-saas-registry'}],
               'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                   'seccompProfile': {'type': 'RuntimeDefault'}},
               'containers': [{'name': 'operation', 'image': image, 'command': command,
                               'env': env or [], 'securityContext': {
                                   'allowPrivilegeEscalation': False,
                                   'capabilities': {'drop': ['ALL']}}}]}
        self.apply({'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
            'name': name, 'namespace': self.ns, 'labels': {'seed-owner': OWNER}},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 360,
                     'template': {'spec': pod}}})
        self.raw('-n', self.ns, 'wait', '--for=condition=complete', 'job/' + name, '--timeout=370s')
        return self.raw('-n', self.ns, 'logs', 'job/' + name)

    def initialize(self):
        sources = self.k('-n', self.ns, 'get', 'gitrepositories.source.toolkit.fluxcd.io')['items']
        if len(sources) != 1:
            raise Blocked('exactly one reviewed lane source required')
        source = sources[0]
        if source['spec'].get('url') != 'https://github.com/ai-workspace-infra/gitops.git':
            raise Blocked('lane repository mismatch')
        ref = source['spec'].get('ref', {})
        if self.r['stage'] == 'sit' and ref.get('commit') != self.r['candidate_sha']:
            raise Blocked('SIT source candidate mismatch')
        if self.r['stage'] == 'uat' and (ref.get('branch') != 'main' or ref.get('commit')):
            raise Blocked('UAT must persistently watch main')
        revision = source.get('status', {}).get('artifact', {}).get('revision', '')
        if not revision.endswith(self.r['candidate_sha']):
            raise Blocked('observed source revision mismatch')
        self.raw('-n', self.ns, 'wait', '--for=condition=Ready',
                 'pod/web-saas-postgresql-0', '--timeout=300s')
        pg = self.k('-n', self.ns, 'get', 'pod', 'web-saas-postgresql-0')
        image_id = pg.get('status', {}).get('containerStatuses', [{}])[0].get('imageID', '')
        if PG_IMAGE.split('@')[1] not in image_id:
            raise Blocked('PostgreSQL runtime imageID mismatch')
        if pg['spec']['containers'][0]['image'] != PG_IMAGE:
            raise Blocked('PostgreSQL image mismatch')
        if self.psql('SHOW log_statement; SHOW log_min_duration_statement; SHOW log_min_duration_sample;').split() != ['none', '-1', '-1']:
            raise Blocked('SQL logging may expose runtime credential statements')
        # A fresh empty target is mandatory. Retries after an interrupted init are
        # blocked for read-only investigation, never auto-reset or overwrite.
        if self.psql("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');").strip() != '0':
            raise Blocked('nonempty target: review required, no reinitialization')
        # No application writers should exist before runtime secret publication.
        if self.psql("SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename <> 'lane_admin';").strip() != '0':
            raise Blocked('target writers not paused')
        def source(repo, sha, path):
            url = f'https://raw.githubusercontent.com/ai-workspace-services/{repo}/{sha}/{path}'
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise Blocked('source size limit')
            return data
        manifest_bytes = source('accounts', ACCOUNTS_SHA, 'sql/init/accounts-native.manifest.json')
        native = source('accounts', ACCOUNTS_SHA, 'sql/init/accounts-native.sql')
        billing = source('billing-service', BILLING_SHA, 'sql/billing-service-schema.sql')
        manifest = json.loads(manifest_bytes)
        tables = verify_sources(manifest, native, billing)
        compiled = json.loads(self.job('manifest', ACCOUNTS_IMAGE, ['/usr/local/bin/migratectl', 'native-schema']))
        if any(compiled.get(key) != manifest.get(key) for key in ('format', 'migration_version', 'schema_sha256', 'business_table_count', 'business_tables')):
            raise Blocked('image compiled schema mismatch')
        existing = {s['metadata']['name'] for s in self.k('-n', self.ns, 'get', 'secrets')['items']}
        if existing.intersection({'web-saas-db-ddl','web-saas-db-probe','web-saas-accounts-runtime','web-saas-billing-runtime','web-saas-console-runtime','web-saas-content-runtime'}):
            raise Blocked('existing seed state: review required, no implicit rotation')
        passwords = {role: secrets.token_hex(32) for role in ('xc_schema_owner','xc_accounts_rw','xc_billing_rw')}
        host = f'web-saas-postgresql.{self.ns}.svc.cluster.local'
        def dsn(role):
            return f'postgresql://{role}:{quote(passwords[role], safe="")}@{host}:5432/account?sslmode=disable'
        ddl = self.secret('web-saas-db-ddl', {'NATIVE_TARGET_DSN': dsn('xc_schema_owner')})
        # Credentials are random hex, identifiers fixed constants. Management SQL
        # remains stdin-only. Fresh DB owner changes before objects exist.
        self.psql(''.join(f"CREATE ROLE {r} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;" for r,pw in passwords.items()) +
                   'ALTER DATABASE account OWNER TO xc_schema_owner; ALTER SCHEMA public OWNER TO xc_schema_owner;')
        env = [{'name': 'NATIVE_TARGET_DSN', 'valueFrom': {'secretKeyRef': {
            'name': 'web-saas-db-ddl', 'key': 'NATIVE_TARGET_DSN'}}}]
        output = self.job('native-init', ACCOUNTS_IMAGE, ['/usr/local/bin/migratectl','init',
            '--environment',self.r['stage'],'--dsn-env','NATIVE_TARGET_DSN',
            '--schema-sha256',SCHEMA_SHA,'--writers-paused','--dry-run=false'], env)
        # A zero exit is necessary, and the actual postcondition is checked below.
        del output, ddl
        self.psql('SET ROLE xc_schema_owner;\n' + billing.decode() + '\n' + grants(tables))
        actual = set(self.psql("SELECT tablename FROM pg_tables WHERE schemaname='public';").split())
        if actual != set(tables) | {'schema_migrations','cloud_vendor_costs'}:
            raise Blocked('unexpected initialized table set')
        # Probe via actual authenticated TCP identities. No administrator SET ROLE
        # substitutes for these connections. Every probe transaction rolls back.
        self.secret('web-saas-db-probe', {
            'ACCOUNTS_PASSWORD': passwords['xc_accounts_rw'],
            'BILLING_PASSWORD': passwords['xc_billing_rw']})
        for role, key, dml in (
            ('xc_accounts_rw', 'ACCOUNTS_PASSWORD',
             "BEGIN; INSERT INTO public.users(username,password,proxy_uuid) VALUES ('seed_probe','local_probe','00000000-0000-4000-8000-000000000001'); SELECT username FROM public.users WHERE username='seed_probe'; ROLLBACK;"),
            ('xc_billing_rw', 'BILLING_PASSWORD',
             "BEGIN; INSERT INTO public.billing_source_sync_state(source_id) VALUES ('seed_probe'); UPDATE public.billing_source_sync_state SET last_error='' WHERE source_id='seed_probe'; SELECT source_id FROM public.billing_source_sync_state WHERE source_id='seed_probe'; ROLLBACK;")):
            probe_env = [{'name': 'PGPASSWORD', 'valueFrom': {'secretKeyRef': {
                'name': 'web-saas-db-probe', 'key': key}}},
                {'name':'PGHOST','value':host}, {'name':'PGPORT','value':'5432'},
                {'name':'PGDATABASE','value':'account'}, {'name':'PGUSER','value':role}]
            # Fixed commands only; credentials are environment SecretRefs.
            script = "set -eu\npsql -XAtq -v ON_ERROR_STOP=1 <<'SQL'\n" + dml + "\nSQL\n"
            rejects = ['CREATE TABLE public.__seed_probe(id int);',
                       'CREATE TEMP TABLE __seed_probe(id int);', 'SET ROLE xc_schema_owner;']
            if role == 'xc_billing_rw':
                rejects.append('SELECT password FROM public.users LIMIT 1;')
            for query in rejects:
                script += "set +e\nresult=$(psql -XAtq -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -c '" + query + "' 2>&1)\nstatus=$?\nset -e\n[ \"$status\" -ne 0 ] || exit 42\nprintf '%s' \"$result\" | grep -q '42501' || exit 43\n"
            script += 'echo identity_probes_verified\n'
            result = self.job('probe-' + role.replace('_','-'), PG_IMAGE,
                              ['/bin/sh', '-c', script], probe_env)
            if not result.rstrip().endswith('identity_probes_verified'):
                raise Blocked('identity probe result missing')
        internal = secrets.token_hex(32)
        self.secret('web-saas-accounts-runtime', {
            'DATABASE_URL': dsn('xc_accounts_rw'),
            'DATABASE_IDENTITY_SHA256': identity(self.ns, 'xc_accounts_rw'),
            'POSTGRES_PASSWORD': passwords['xc_accounts_rw'],
            'ROOT_BOOTSTRAP_PASSWORD': secrets.token_hex(32),
            'AUTH_TOKEN_PUBLIC_TOKEN': secrets.token_hex(32),
            'AUTH_TOKEN_ACCESS_SECRET': secrets.token_hex(32),
            'AUTH_TOKEN_REFRESH_SECRET': secrets.token_hex(32),
            'AGENT_API_TOKEN': internal})
        self.secret('web-saas-billing-runtime', {
            'DATABASE_URL': dsn('xc_billing_rw'), 'INTERNAL_SERVICE_TOKEN': internal})
        for service in ('console', 'content'):
            self.secret('web-saas-' + service + '-runtime', {'INTERNAL_SERVICE_TOKEN':internal})
        final_source = self.k('-n', self.ns, 'get', 'gitrepository', source['metadata']['name'])
        if final_source.get('status', {}).get('artifact', {}).get('revision') != revision:
            raise Blocked('candidate changed during initialization')
        return {'schema_version': 'dev-lane-seed/v1', 'result': 'verified',
                'instance_id': self.r['instance_id'], 'node_uid': self.r['node_uid'],
                'cluster_uid': self.r['cluster_uid'], 'namespace': self.ns,
                'candidate_sha': self.r['candidate_sha'],
                'database_roles': ['xc_accounts_rw','xc_billing_rw'],
                'schema_initialized': True}



def main():
    try:
        request = json.load(sys.stdin)
        owner = Owner(request)
        if request['phase'] == 'seed-admin':
            owner.secret('web-saas-db-admin', {'password': secrets.token_hex(32)})
            result = {'result': 'admin_secret_prepared', 'namespace': owner.ns}
        elif request['phase'] == 'initialize':
            result = owner.initialize()
        else:
            raise Blocked('unsupported phase')
        print(json.dumps(result))
    except Exception:
        print(json.dumps({'result': 'BLOCKED', 'reason': 'seed owner failed; inspect sanitized phase evidence'}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
