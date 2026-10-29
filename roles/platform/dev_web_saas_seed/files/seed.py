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
from urllib.parse import quote, urlsplit
from platform_scope import preflight, failure_code
from contracts import ACCOUNTS_SHA, BILLING_SHA, SCHEMA_SHA, target, identity, verify_sources, verify_tagged_sources, grants, audit_grants

ACCOUNTS_IMAGE = 'ghcr.io/ai-workspace-services/accounts@sha256:6edd156ce6974dfd0cd7486beebbb446091e4019eacf3c272e05aba4a609737d'
PG_AMD64_DIGEST = 'sha256:f326b029499b2f255f5341d0a06d710c4380e6683315c8ab367b25c7069bdfca'
PG_IMAGE = 'ghcr.io/ai-workspace-services/postgresql@sha256:c6ee94d4c91054128823e816e936e03702eee80fdcc04d3271a7e68167974ad2'
OWNER = 'dev-web-saas-seed-v1'
# Explicit user authorization applies only to the frozen rehearsal candidate.
SIT_RLS_CANDIDATE = 'c470c495623f45ae0f98bf641bcdd49a42d4459c9c3eec0460ea03980b9d8b23'
SIT_RLS_TABLES = ('account_lifecycle_events','finance_invoices','finance_operation_events',
                  'finance_operations','finance_payments','finance_refunds','mfa_recovery_codes',
                  'mfa_reset_requests','network_team_members','network_teams','overlay_config_acks',
                  'overlay_nodes','password_recovery_challenges','subscription_lifecycle_states',
                  'team_agent_enrollments')


class Blocked(Exception):
    pass


class Owner:
    def __init__(self, request):
        self.r = request
        target(request['stage'], request['namespace'], request.get('tagged_lane'))
        self.ns = request['namespace']
        config = {**request, 'environment':request['stage'], 'mode':'prepare', 'node_name':'dev-k3s-v5'}
        source, root = ('platform-config-dev', 'platform-ingress-dev') if request.get('tagged_lane') is not None else ('platform-config', 'platform-root')
        preflight(config, self.k('get','nodes'), self.k('get','namespace','kube-system'),
                  self.k('-n','flux-system','get','gitrepository',source),
                  self.k('-n','flux-system','get','kustomization',root))
        for key in ('candidate_sha',):
            if not re.fullmatch('[0-9a-f]{40}', request[key]):
                raise Blocked('invalid candidate')
        for key in ('instance_id', 'node_uid', 'cluster_uid', 'namespace_uid'):
            if not isinstance(request[key], str) or not request[key]:
                raise Blocked('missing trusted target identity')
        # The parent caller must bind instance_id to CMDB before SSH. This checks
        # the independently supplied in-cluster identity again, not API credentials.
        if self.k('get', 'namespace', 'kube-system')['metadata']['uid'] != request['cluster_uid']:
            raise Blocked('cluster identity mismatch')
        nodes = self.k('get', 'nodes')['items']
        if len(nodes) != 1 or nodes[0]['metadata'].get('labels', {}).get('kubernetes.io/arch') != 'amd64':
            raise Blocked('reviewed amd64 runtime mapping required')
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != request['node_uid']:
            raise Blocked('single-node identity mismatch')
        self.check_namespace()

    def tagged_inputs(self):
        """Recheck typed identities supplied by the authenticated seed adapter."""
        lane = self.r.get('tagged_lane')
        if lane is None:
            return None
        target(self.r['stage'], self.ns, lane)
        inputs = self.r.get('data_baseline_inputs')
        runtime = self.r.get('runtime_mapping')
        if not isinstance(inputs, dict) or not isinstance(runtime, dict):
            raise Blocked('missing authenticated seed baseline or OCI mapping')
        encoded = json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(encoded).hexdigest() != self.r.get('data_baseline_sha256'):
            raise Blocked('baseline digest mismatch')
        records = runtime.get('images', {})
        if not isinstance(records, dict) or not isinstance(records.get('postgresql'), dict):
            raise Blocked('missing PostgreSQL OCI mapping')
        pg = records['postgresql']
        image = pg.get('image', '')
        child = pg.get('platform_manifest_digest', '')
        if (not re.fullmatch(r'ghcr\.io/ai-workspace-services/postgresql@sha256:[0-9a-f]{64}', image) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', child) or pg.get('manifest_digest') != image.split('@')[1] or
            pg.get('platform') != 'linux/amd64'):
            raise Blocked('invalid PostgreSQL OCI identity')
        return inputs, image, child

    def check_namespace(self):
        ns = self.k('get', 'namespace', self.ns)
        if ns['metadata'].get('uid') != self.r['namespace_uid']:
            raise Blocked('namespace UID changed or stale target')
        if ns['metadata'].get('deletionTimestamp'):
            raise Blocked('namespace is terminating')
        if ns['metadata'].get('labels', {}).get('environment') != self.r['stage']:
            raise Blocked('namespace stage mismatch')
        lane = self.r.get('tagged_lane')
        if lane is not None and (ns['metadata'].get('labels', {}).get('feature_id') != lane['feature_id'] or
                                 ns['metadata'].get('labels', {}).get('release.svc.plus/feature-tag') != lane['feature_tag']):
            raise Blocked('namespace feature label mismatch')

    def seed_admin(self):
        self.check_namespace()
        self.tagged_inputs()
        self.secret('web-saas-db-admin', {'password': secrets.token_hex(32)})
        self.check_namespace()
        return {'result':'admin_secret_prepared', 'namespace':self.ns,
                'namespace_uid':self.r['namespace_uid']}

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

    def psql(self, sql, sensitive=False):
        # Only this management connection disables error-statement logging.
        # Startup options take effect before parsing any password-bearing SQL;
        # a preceding SET in the same SQL message would not protect parse errors.
        command = []
        if sensitive:
            command = ['env', 'PGOPTIONS=-c log_min_error_statement=panic -c log_statement=none -c log_min_duration_statement=-1 -c log_min_duration_sample=-1']
            sql = """DO $$ BEGIN
IF current_setting('log_min_error_statement') <> 'panic'
 OR current_setting('log_statement') <> 'none'
 OR current_setting('log_min_duration_statement') <> '-1'
 OR current_setting('log_min_duration_sample') <> '-1'
 OR current_setting('shared_preload_libraries') <> ''
 OR current_setting('session_preload_libraries') <> ''
 OR current_setting('local_preload_libraries') <> ''
 OR EXISTS (SELECT 1 FROM pg_extension WHERE extname IN ('pgaudit','pg_stat_statements'))
 OR COALESCE(current_setting('pgaudit.log',true),'none') NOT IN ('none','')
 THEN RAISE EXCEPTION 'credential SQL logging protection unavailable';
END IF; END $$;
""" + sql
        return self.raw('-n', self.ns, 'exec', '-i', 'web-saas-postgresql-0', '--',
                        *command, 'psql', '-XAtq', '-U', 'lane_admin', '-d', 'account',
                        '-v', 'ON_ERROR_STOP=1', data=sql)

    def job(self, purpose, image, command, env=None, wait_postgres=None):
        name = 'dev-seed-' + purpose + '-' + secrets.token_hex(4)
        pod = {'automountServiceAccountToken': False, 'restartPolicy': 'Never',
               'imagePullSecrets': [{'name': 'web-saas-registry'}],
               'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001,
                                   'seccompProfile': {'type': 'RuntimeDefault'}},
               'containers': [{'name': 'operation', 'image': image, 'command': command,
                               'env': env or [], 'securityContext': {
                                   'allowPrivilegeEscalation': False,
                                   'capabilities': {'drop': ['ALL']}}}]}
        if wait_postgres is not None:
            host = f'web-saas-postgresql.{self.ns}.svc.cluster.local'
            pod['initContainers'] = [{'name': 'wait-for-lane-postgres', 'image': wait_postgres,
                'command': ['/bin/sh', '-c', 'for i in $(seq 1 60); do pg_isready -t 2 -h "$PGHOST" -p 5432 -U lane_admin -d account && exit 0; sleep 2; done; exit 1'],
                'env': [{'name': 'PGHOST', 'value': host}], 'securityContext': {
                    'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}}]
        self.apply({'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
            'name': name, 'namespace': self.ns, 'labels': {'seed-owner': OWNER}},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 360,
                     'template': {'spec': pod}}})
        self.raw('-n', self.ns, 'wait', '--for=condition=complete', 'job/' + name, '--timeout=370s')
        return self.raw('-n', self.ns, 'logs', 'job/' + name)

    def validate_empty_recovery(self, host, passwords, require_empty=True):
        # Only resume a failed, unpublished, empty SIT initialization. No
        # application secret may exist and every seed job must be terminal.
        self.check_namespace()
        jobs = self.k('-n', self.ns, 'get', 'jobs')['items']
        if any(job.get('status', {}).get('active', 0) for job in jobs):
            raise Blocked('active jobs prevent partial seed recovery')
        empty = self.psql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f'); SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'; SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%';").split()
        if require_empty and empty != ['0','0','0']:
            raise Blocked('partial seed contains schema objects or extra schemas')
        roles = set(self.psql("SELECT rolname FROM pg_roles WHERE rolname IN ('xc_schema_owner','xc_accounts_rw','xc_billing_rw','xc_lane_audit') AND rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);").split())
        if roles != set(passwords):
            raise Blocked('partial seed role identities require review')
        if self.psql("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid IN (m.roleid,m.member) WHERE r.rolname IN ('xc_schema_owner','xc_accounts_rw','xc_billing_rw','xc_lane_audit');").strip() != '0':
            raise Blocked('partial seed role memberships require review')
        if self.psql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='account';").strip() != 'xc_schema_owner':
            raise Blocked('partial seed database owner requires review')
        if self.psql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public';").strip() != 'xc_schema_owner':
            raise Blocked('partial seed schema owner requires review')
        ddl = self.secret('web-saas-db-ddl', {'NATIVE_TARGET_DSN': ''})
        parsed = urlsplit(ddl['NATIVE_TARGET_DSN'])
        if (parsed.scheme != 'postgresql' or parsed.username != 'xc_schema_owner' or parsed.hostname != host or parsed.port != 5432 or parsed.path != '/account' or parsed.query != 'sslmode=disable' or not re.fullmatch(r'[0-9a-f]{64}', parsed.password or '')):
            raise Blocked('partial seed DDL credential contract mismatch')
        return parsed.password

    def validate_native_continuation(self, tables, inputs, accounts_image, schema_sha, seeded=False):
        # Recover only from an authenticated exact-image native job, an exact
        # migration/table set, and a database with no application data/writers.
        if not inputs or self.r.get('tagged_lane', {}).get('feature_id') != 'sit-test-1483':
            raise Blocked('native continuation is outside the approved lane')
        jobs = self.k('-n', self.ns, 'get', 'jobs')['items']
        if any(j.get('status', {}).get('active', 0) for j in jobs):
            raise Blocked('active jobs prevent native continuation')
        expected_cmd = ['/usr/local/bin/migratectl','init','--environment','sit','--dsn-env','NATIVE_TARGET_DSN','--schema-sha256',schema_sha,'--writers-paused','--dry-run=false']
        expected_env = [{'name':'NATIVE_TARGET_DSN','valueFrom':{'secretKeyRef':{'name':'web-saas-db-ddl','key':'NATIVE_TARGET_DSN'}}}]
        found = []
        for job in jobs:
            meta = job.get('metadata', {}); status = job.get('status', {})
            containers = job.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            if (meta.get('namespace') == self.ns and meta.get('name','').startswith('dev-seed-native-init-') and meta.get('labels',{}).get('seed-owner') == OWNER and status.get('succeeded') == 1 and any(x.get('type') == 'Complete' and x.get('status') == 'True' for x in status.get('conditions',[])) and len(containers) == 1 and containers[0].get('image') == accounts_image and containers[0].get('command') == expected_cmd and containers[0].get('env') == expected_env and re.fullmatch(r'[0-9a-f-]{36}',meta.get('uid',''))):
                found.append(job)
        if len(found) != 1:
            raise Blocked('unique authenticated native initialization job required')
        job = found[0]; name = job['metadata']['name']; uid = job['metadata']['uid']
        pods = self.k('-n', self.ns, 'get', 'pods', '-l', 'job-name='+name)['items']
        if len(pods) != 1:
            raise Blocked('native initialization pod identity mismatch')
        pod = pods[0]; statuses = pod.get('status', {}).get('containerStatuses', [])
        mapped = self.r['runtime_mapping']['images']['accounts']; image_id = statuses[0].get('imageID','') if len(statuses) == 1 else ''
        allowed = {accounts_image, 'docker-pullable://' + accounts_image, accounts_image.split('@')[0] + '@' + mapped['platform_manifest_digest'], 'docker-pullable://' + accounts_image.split('@')[0] + '@' + mapped['platform_manifest_digest']}
        if (pod.get('status', {}).get('phase') != 'Succeeded' or not any(ref.get('uid') == uid and ref.get('controller') is True for ref in pod.get('metadata',{}).get('ownerReferences',[])) or len(statuses) != 1 or statuses[0].get('state',{}).get('terminated',{}).get('exitCode') != 0 or image_id not in allowed):
            raise Blocked('native initialization runtime evidence mismatch')
        actual = set(self.psql("SELECT tablename FROM pg_tables WHERE schemaname='public';").split())
        expected = set(tables) | {'schema_migrations'} | ({'cloud_vendor_costs'} if seeded else set())
        if actual != expected:
            raise Blocked('native continuation table set mismatch')
        if self.psql('SELECT version,dirty FROM public.schema_migrations;').strip() != str(inputs['migration_version']) + '|f':
            raise Blocked('native continuation migration version mismatch')
        if self.psql(''.join('SELECT count(*) FROM public.' + table + ';' for table in tables)).split() != ['0'] * len(tables):
            raise Blocked('native continuation contains business data')
        if seeded and self.psql('SELECT count(*) FROM public.cloud_vendor_costs;').strip() != '0':
            raise Blocked('scope repair cost table is not empty')
        activity = "SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename NOT IN ('lane_admin','xc_billing_rw');" if seeded else "SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename <> 'lane_admin';"
        if self.psql(activity).strip() != '0':
            raise Blocked('native continuation has unexpected connections')
        return uid

    def verify_role_identities(self, pg_image, host, tagged):
        # Probe via actual authenticated TCP identities. No administrator SET ROLE
        # substitutes for these connections. Every probe transaction rolls back.
        for role, key, dml in (
            ('xc_accounts_rw', 'ACCOUNTS_PASSWORD',
             "BEGIN; INSERT INTO public.users(username,password,proxy_uuid) VALUES ('seed_probe','local_probe','00000000-0000-4000-8000-000000000001'); SELECT username FROM public.users WHERE username='seed_probe'; " + (('SELECT count(*) FROM public.cloud_vendor_costs; ' + ''.join('SELECT count(*) FROM public.' + name + ';' for name in SIT_RLS_TABLES)) if tagged and (self.r.get('tagged_lane') or {}).get('candidate_sha256') == SIT_RLS_CANDIDATE else ('SELECT count(*) FROM public.cloud_vendor_costs; ' if tagged else '')) + "ROLLBACK;"),
            ('xc_billing_rw', 'BILLING_PASSWORD',
             "BEGIN; INSERT INTO public.billing_source_sync_state(source_id) VALUES ('seed_probe'); UPDATE public.billing_source_sync_state SET last_error='' WHERE source_id='seed_probe'; SELECT source_id FROM public.billing_source_sync_state WHERE source_id='seed_probe'; ROLLBACK;"),
            ('xc_lane_audit','AUDIT_PASSWORD',
             'SELECT version, dirty FROM ' + ('lane_audit' if tagged else 'public') + '.dev_lane_schema_status;')):
            probe_env = [{'name': 'PGPASSWORD', 'valueFrom': {'secretKeyRef': {
                'name': 'web-saas-db-probe', 'key': key}}},
                {'name':'PGHOST','value':host}, {'name':'PGPORT','value':'5432'},
                {'name':'PGDATABASE','value':'account'}, {'name':'PGUSER','value':role}]
            # Fixed commands only; credentials are environment SecretRefs.
            script = "set -eu\npsql -XAtq -v ON_ERROR_STOP=1 <<'SQL'\n" + dml + "\nSQL\n"
            rejects = ['CREATE TABLE public.__seed_probe(id int);',
                       'CREATE TEMP TABLE __seed_probe(id int);', 'SET ROLE xc_schema_owner;']
            if role == 'xc_accounts_rw' and tagged:
                rejects.append('UPDATE public.cloud_vendor_costs SET provider=provider;')
            if role in ('xc_billing_rw','xc_lane_audit'):
                rejects.append('SELECT password FROM public.users LIMIT 1;')
            if role == 'xc_lane_audit':
                if tagged and (self.r.get('tagged_lane') or {}).get('candidate_sha256') == SIT_RLS_CANDIDATE:
                    rejects.append('SELECT count(*) FROM public.finance_invoices;')
                rejects.extend(["INSERT INTO public.billing_source_sync_state(source_id) VALUES (NULL);",
                                'UPDATE public.schema_migrations SET dirty=true;',
                                'SET ROLE xc_accounts_rw;'])
            for query in rejects:
                script += "set +e\nresult=$(psql -XAtq -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -c '" + query + "' 2>&1)\nstatus=$?\nset -e\n[ \"$status\" -ne 0 ] || exit 42\nprintf '%s' \"$result\" | grep -q '42501' || exit 43\n"
            script += 'echo identity_probes_verified\n'
            result = self.job('probe-' + role.replace('_','-'), pg_image,
                              ['/bin/sh', '-c', script], probe_env, wait_postgres=pg_image)
            if not result.rstrip().endswith('identity_probes_verified'):
                raise Blocked('identity probe result missing')

    def ensure_authorized_rehearsal_rls(self):
        lane = self.r.get('tagged_lane') or {}
        if (self.ns != 'feature-sit-test-1483-sit' or lane.get('feature_id') != 'sit-test-1483' or
            lane.get('feature_tag') != 'smoke-sit-test-1483' or lane.get('candidate_sha256') != SIT_RLS_CANDIDATE):
            raise Blocked('RLS policy authorization is outside this fixed rehearsal')
        actual = set(self.psql("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity;").split())
        if actual != set(SIT_RLS_TABLES):
            raise Blocked('reviewed RLS table or ownership scope changed')
        invalid = self.psql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity AND (c.relforcerowsecurity OR pg_get_userbyid(c.relowner)<>'xc_schema_owner');").strip()
        if invalid != '0':
            raise Blocked('reviewed RLS table or ownership scope changed')
        policy_query = "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity; SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity AND p.polname='accounts_runtime_access' AND p.polcmd='*' AND p.polpermissive AND p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname='xc_accounts_rw')] AND pg_get_expr(p.polqual,p.polrelid)='true' AND pg_get_expr(p.polwithcheck,p.polrelid)='true';"
        counts = self.psql(policy_query).split()
        if counts == ['0','0']:
            commands = ''.join('CREATE POLICY accounts_runtime_access ON public.' + name + ' FOR ALL TO xc_accounts_rw USING (true) WITH CHECK (true);' for name in SIT_RLS_TABLES)
            self.psql("BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL ROLE xc_schema_owner; " + commands + ' COMMIT;')
        elif counts != [str(len(SIT_RLS_TABLES)), str(len(SIT_RLS_TABLES))]:
            raise Blocked('existing RLS policy state requires review')
        if self.psql(policy_query).split() != [str(len(SIT_RLS_TABLES)), str(len(SIT_RLS_TABLES))]:
            raise Blocked('RLS policy verification failed')
        return len(SIT_RLS_TABLES)

    def repair_seeded_scope(self, tables, inputs, accounts_image, schema_sha, pg_image, revision, source_record):
        lane = self.r.get('tagged_lane') or {}
        if lane.get('feature_id') != 'sit-test-1483' or self.ns != 'feature-sit-test-1483-sit':
            raise Blocked('scope repair is outside the approved test lane')
        host = f'web-saas-postgresql.{self.ns}.svc.cluster.local'
        passwords = {role: '' for role in ('xc_schema_owner','xc_accounts_rw','xc_billing_rw','xc_lane_audit')}
        self.validate_empty_recovery(host, passwords, require_empty=False)
        prior_uid = self.validate_native_continuation(tables, inputs, accounts_image, schema_sha, seeded=True)
        self.check_namespace()
        state = self.psql("SELECT COALESCE(to_regclass('public.dev_lane_schema_status')::text,'none'), COALESCE(to_regclass('lane_audit.dev_lane_schema_status')::text,'none');").strip()
        if state not in ('dev_lane_schema_status|none', 'none|lane_audit.dev_lane_schema_status'):
            raise Blocked('audit view placement requires review')
        view = 'public.dev_lane_schema_status' if state.startswith('dev_lane_schema_status|') else 'lane_audit.dev_lane_schema_status'
        definition = ' '.join(self.psql("SELECT pg_get_viewdef('" + view + "'::regclass, true);").split())
        if definition != 'SELECT version, dirty FROM schema_migrations;':
            raise Blocked('audit view definition requires review')
        relations = set(self.psql("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f');").split())
        expected = set(tables) | {'schema_migrations', 'cloud_vendor_costs'} | ({'dev_lane_schema_status'} if view.startswith('public.') else set())
        if relations != expected:
            raise Blocked('public Accounts catalog differs from fixed candidate')
        audit_owner = self.psql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='lane_audit';").strip()
        if audit_owner not in ('', 'xc_schema_owner'):
            raise Blocked('audit schema ownership requires review')
        owners = self.psql("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid IN ('public.cloud_vendor_costs'::regclass, '" + view + "'::regclass) ORDER BY oid;").split()
        if owners != ['xc_schema_owner','xc_schema_owner']:
            raise Blocked('scope repair object ownership requires review')
        # Only the audit view moves; Billing data stays in its canonical public
        # table. No runtime password, table contents, or other lane is changed.
        move = 'ALTER VIEW public.dev_lane_schema_status SET SCHEMA lane_audit;' if view.startswith('public.') else ''
        self.psql("BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL ROLE xc_schema_owner; CREATE SCHEMA IF NOT EXISTS lane_audit AUTHORIZATION xc_schema_owner; REVOKE ALL ON SCHEMA lane_audit FROM PUBLIC; " + move + ' GRANT USAGE ON SCHEMA lane_audit TO xc_lane_audit; REVOKE USAGE ON SCHEMA public FROM xc_lane_audit; GRANT SELECT ON public.cloud_vendor_costs TO xc_accounts_rw; COMMIT;')
        rls_count = self.ensure_authorized_rehearsal_rls()
        self.verify_role_identities(pg_image, host, True)
        self.check_namespace()
        final_source = self.k('-n', self.ns, 'get', 'gitrepository', source_record['metadata']['name'])
        if final_source.get('status', {}).get('artifact', {}).get('revision') != revision:
            raise Blocked('candidate changed during scope repair')
        return {'schema_version':'dev-lane-seed/v1', 'result':'verified', 'instance_id':self.r['instance_id'],
                'node_uid':self.r['node_uid'], 'cluster_uid':self.r['cluster_uid'], 'namespace':self.ns,
                'namespace_uid':self.r['namespace_uid'], 'candidate_sha':self.r['candidate_sha'],
                'database_roles':['xc_accounts_rw','xc_billing_rw'], 'schema_initialized':True,
                'seed_recovery':'audit_scope_isolation', 'prior_native_job_uid':prior_uid, 'accounts_rls_policy_count':rls_count,
                'audit_role':'xc_lane_audit', 'audit_metadata_read_verified':True,
                'audit_write_and_sensitive_read_denied':True, 'feature_id':lane['feature_id'],
                'feature_tag':lane['feature_tag'], 'candidate_sha256':lane['candidate_sha256'],
                'data_baseline_sha256':self.r['data_baseline_sha256']}

    def initialize(self):
        self.check_namespace()
        tagged = self.tagged_inputs()
        inputs, pg_image, pg_child = tagged if tagged else (None, PG_IMAGE, PG_AMD64_DIGEST)
        accounts_image = inputs['accounts_image'] if inputs else ACCOUNTS_IMAGE
        accounts_sha = inputs['accounts_sha'] if inputs else ACCOUNTS_SHA
        billing_sha = inputs['billing_sha'] if inputs else BILLING_SHA
        schema_sha = inputs['accounts_native_sha256'] if inputs else SCHEMA_SHA
        sources = self.k('-n', self.ns, 'get', 'gitrepositories.source.toolkit.fluxcd.io')['items']
        if len(sources) != 1:
            raise Blocked('exactly one reviewed lane source required')
        source_record = sources[0]
        if source_record['spec'].get('url') != 'https://github.com/ai-workspace-infra/gitops.git':
            raise Blocked('lane repository mismatch')
        ref = source_record['spec'].get('ref', {})
        if self.r['stage'] == 'sit' and ref.get('commit') != self.r['candidate_sha']:
            raise Blocked('SIT source candidate mismatch')
        revision = source_record.get('status', {}).get('artifact', {}).get('revision', '')
        if not revision.endswith(self.r['candidate_sha']):
            raise Blocked('observed source revision mismatch')
        self.raw('-n', self.ns, 'wait', '--for=condition=Ready',
                 'pod/web-saas-postgresql-0', '--timeout=300s')
        pg = self.k('-n', self.ns, 'get', 'pod', 'web-saas-postgresql-0')
        image_id = pg.get('status', {}).get('containerStatuses', [{}])[0].get('imageID', '')
        if image_id not in (pg_image,
                            'docker-pullable://' + pg_image,
                            'ghcr.io/ai-workspace-services/postgresql@' + pg_child,
                            'docker-pullable://ghcr.io/ai-workspace-services/postgresql@' + pg_child):
            raise Blocked('PostgreSQL runtime imageID mismatch')
        if pg['spec']['containers'][0]['image'] != pg_image:
            raise Blocked('PostgreSQL image mismatch')
        if self.psql('SHOW log_statement; SHOW log_min_duration_statement; SHOW log_min_duration_sample;').split() != ['none', '-1', '-1']:
            raise Blocked('SQL logging may expose runtime credential statements')
        secret_items = self.k('-n', self.ns, 'get', 'secrets')['items']
        owned = {row['metadata']['name']: row['metadata'].get('labels', {}).get('seed-owner') for row in secret_items}
        runtime_names = {'web-saas-db-ddl','web-saas-db-probe','web-saas-db-audit','web-saas-accounts-runtime','web-saas-billing-runtime','web-saas-console-runtime','web-saas-content-runtime'}
        seeded = bool(tagged and self.r.get('tagged_lane', {}).get('feature_id') == 'sit-test-1483' and runtime_names <= set(owned))
        if seeded and any(owned[name] != OWNER for name in runtime_names):
            raise Blocked('existing runtime secrets have unknown owner')
        # A fresh empty target is mandatory. Retries after an interrupted init are
        # blocked for read-only investigation, never auto-reset or overwrite.
        existing_table_count = self.psql("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');").strip()
        if existing_table_count != '0' and (not tagged or self.r.get('tagged_lane', {}).get('feature_id') != 'sit-test-1483'):
            raise Blocked('nonempty target: review required, no reinitialization')
        # No application writers should exist before runtime secret publication.
        if not seeded and self.psql("SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename <> 'lane_admin';").strip() != '0':
            raise Blocked('target writers not paused')
        def fetch_source(repo, sha, path):
            url = f'https://raw.githubusercontent.com/ai-workspace-services/{repo}/{sha}/{path}'
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise Blocked('source size limit')
            return data
        manifest_bytes = fetch_source('accounts', accounts_sha, 'sql/init/accounts-native.manifest.json')
        native = fetch_source('accounts', accounts_sha, 'sql/init/accounts-native.sql')
        billing = fetch_source('billing-service', billing_sha, 'sql/billing-service-schema.sql')
        manifest = json.loads(manifest_bytes)
        compiled_bytes = self.job('manifest', accounts_image, ['/usr/local/bin/migratectl', 'native-schema']).encode()
        tables = (verify_tagged_sources(manifest_bytes, native, billing, compiled_bytes, inputs)
                  if inputs else verify_sources(manifest, native, billing))
        compiled = json.loads(compiled_bytes)
        if any(compiled.get(key) != manifest.get(key) for key in ('format', 'migration_version', 'schema_sha256', 'business_table_count', 'business_tables')):
            raise Blocked('image compiled schema mismatch')
        if seeded:
            return self.repair_seeded_scope(tables, inputs, accounts_image, schema_sha, pg_image, revision, source_record)
        existing = {s['metadata']['name'] for s in self.k('-n', self.ns, 'get', 'secrets')['items']}
        seed_secrets = existing.intersection({'web-saas-db-ddl','web-saas-db-probe','web-saas-db-audit','web-saas-accounts-runtime','web-saas-billing-runtime','web-saas-console-runtime','web-saas-content-runtime'})
        recovery = bool(seed_secrets)
        native_continuation = existing_table_count != '0'
        if native_continuation and seed_secrets != {'web-saas-db-ddl','web-saas-db-probe'}:
            raise Blocked('native continuation requires exact unpublished seed credentials')
        lane = self.r.get('tagged_lane') or {}
        if recovery and (lane.get('feature_id') != 'sit-test-1483' or seed_secrets not in ({'web-saas-db-ddl'}, {'web-saas-db-ddl','web-saas-db-probe'})):
            raise Blocked('existing seed state: review required, no implicit rotation')
        passwords = {role: secrets.token_hex(32) for role in ('xc_schema_owner','xc_accounts_rw','xc_billing_rw','xc_lane_audit')}
        host = f'web-saas-postgresql.{self.ns}.svc.cluster.local'
        def dsn(role):
            return f'postgresql://{role}:{quote(passwords[role], safe="")}@{host}:5432/account?sslmode=disable'
        if recovery:
            passwords['xc_schema_owner'] = self.validate_empty_recovery(host, passwords, require_empty=not native_continuation)
        else:
            ddl = self.secret('web-saas-db-ddl', {'NATIVE_TARGET_DSN': dsn('xc_schema_owner')})
        # Persist unpublished business passwords before creating or updating roles;
        # a retry reuses exactly the same owner-labelled secret.
        probe = self.secret('web-saas-db-probe', {'ACCOUNTS_PASSWORD': passwords['xc_accounts_rw'], 'BILLING_PASSWORD': passwords['xc_billing_rw'], 'AUDIT_PASSWORD': passwords['xc_lane_audit']})
        for role, key in (('xc_accounts_rw','ACCOUNTS_PASSWORD'),('xc_billing_rw','BILLING_PASSWORD'),('xc_lane_audit','AUDIT_PASSWORD')):
            if not re.fullmatch(r'[0-9a-f]{64}', probe[key]):
                raise Blocked('partial seed probe credential contract mismatch')
            passwords[role] = probe[key]
        self.check_namespace()
        if self.psql("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');").strip() != existing_table_count or self.psql("SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename <> 'lane_admin';").strip() != '0':
            raise Blocked('partial seed target changed before role preparation')
        if native_continuation:
            prior_native_uid = self.validate_native_continuation(tables, inputs, accounts_image, schema_sha)
        elif recovery:
            self.psql(''.join(f"ALTER ROLE {r} PASSWORD '{passwords[r]}';" for r in ('xc_accounts_rw','xc_billing_rw','xc_lane_audit')), sensitive=True)
        else:
            self.psql(''.join(f"CREATE ROLE {r} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;" for r,pw in passwords.items()) +
                       'ALTER DATABASE account OWNER TO xc_schema_owner; ALTER SCHEMA public OWNER TO xc_schema_owner;', sensitive=True)
        if not native_continuation:
            env = [{'name': 'NATIVE_TARGET_DSN', 'valueFrom': {'secretKeyRef': {
                'name': 'web-saas-db-ddl', 'key': 'NATIVE_TARGET_DSN'}}}]
            self.job('native-init', accounts_image, ['/usr/local/bin/migratectl','init',
                '--environment',self.r['stage'],'--dsn-env','NATIVE_TARGET_DSN',
                '--schema-sha256',schema_sha,'--writers-paused','--dry-run=false'], env, wait_postgres=pg_image)
        self.psql('BEGIN; SET LOCAL ROLE xc_schema_owner;\n' + billing.decode() + '\n' + grants(tables, expected_count=inputs['business_table_count'] if inputs else 77, tagged=bool(tagged)) + '\nCOMMIT;')
        actual = set(self.psql("SELECT tablename FROM pg_tables WHERE schemaname='public';").split())
        if actual != set(tables) | {'schema_migrations','cloud_vendor_costs'}:
            raise Blocked('unexpected initialized table set')
        if self.psql("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prosecdef;").strip() != '0':
            raise Blocked('unexpected SECURITY DEFINER function requires review')
        self.psql('SET ROLE xc_schema_owner;\n' + audit_grants(tagged=bool(tagged)))
        self.verify_role_identities(pg_image, host, tagged)
        self.secret('web-saas-db-audit', {'DATABASE_URL':dsn('xc_lane_audit')})
        internal = secrets.token_hex(32)
        self.secret('web-saas-accounts-runtime', {
            'DATABASE_URL': dsn('xc_accounts_rw'),
            'DATABASE_IDENTITY_SHA256': identity(self.ns, 'xc_accounts_rw', self.r.get('tagged_lane')),
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
        final_source = self.k('-n', self.ns, 'get', 'gitrepository', source_record['metadata']['name'])
        if final_source.get('status', {}).get('artifact', {}).get('revision') != revision:
            raise Blocked('candidate changed during initialization')
        self.check_namespace()
        receipt = {'schema_version': 'dev-lane-seed/v1', 'result': 'verified',
                'instance_id': self.r['instance_id'], 'node_uid': self.r['node_uid'],
                'cluster_uid': self.r['cluster_uid'], 'namespace': self.ns,
                'namespace_uid': self.r['namespace_uid'],
                'candidate_sha': self.r['candidate_sha'],
                'database_roles': ['xc_accounts_rw','xc_billing_rw'],
                'schema_initialized': True, 'seed_recovery': 'verified_native_initialized' if native_continuation else ('empty_unpublished_roles' if recovery else 'none'), 'audit_role':'xc_lane_audit',
                'audit_metadata_read_verified':True, 'audit_write_and_sensitive_read_denied':True}
        if native_continuation:
            receipt['prior_native_job_uid'] = prior_native_uid
        if tagged:
            receipt.update(feature_id=self.r['tagged_lane']['feature_id'],
                           feature_tag=self.r['tagged_lane']['feature_tag'],
                           candidate_sha256=self.r['tagged_lane']['candidate_sha256'],
                           data_baseline_sha256=self.r['data_baseline_sha256'])
        return receipt



def main():
    request = {}
    try:
        request = json.load(sys.stdin)
        owner = Owner(request)
        if request['phase'] == 'seed-admin':
            result = owner.seed_admin()
        elif request['phase'] == 'initialize':
            result = owner.initialize()
        else:
            raise Blocked('unsupported phase')
        print(json.dumps(result))
    except Exception as error:
        phase = request.get('phase') if isinstance(request, dict) else None
        operation = phase if phase in ('seed-admin', 'initialize') else 'unknown'
        print(json.dumps({'result': 'BLOCKED', 'failure_code': failure_code(operation, error),
                          'reason': 'seed owner failed; inspect sanitized phase evidence'}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
