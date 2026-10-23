"""Opt-in real Accounts API -> native encrypted backup/restore acceptance.

Run with CONTROL_PLANE_API_RESTORE_FIXTURE=1, CONTROL_PLANE_ACCOUNTS_SOURCE
pointing to a clean Accounts checkout, and CONTROL_PLANE_POSTGRES_IMAGE pinned
to postgres@sha256:<digest>. No existing container, database or service is used.
Optional CONTROL_PLANE_ACCOUNTS_SHA pins the expected source commit;
CONTROL_PLANE_PLAYBOOKS_SHA pins the exact committed Playbooks revision.
CONTROL_PLANE_BASH selects Bash >=4. The fixture builds the native CLI itself.
CONTROL_PLANE_API_RESTORE_REPORT may name a new private JSON file under /tmp
for aggregate proof after cleanup (no credentials or row data).
Only disposable Docker clusters and private temporary files are mutated.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
OPT_IN = os.environ.get('CONTROL_PLANE_API_RESTORE_FIXTURE') == '1'

# A small external helper uses the same bcrypt/TOTP libraries as Accounts.
# Credentials are passed via stdin and captured in memory, never logged/files.
AUTH_HELPER = r'''package main
import (
 "encoding/json"
 "os"
 "time"
 "github.com/pquerna/otp/totp"
 "golang.org/x/crypto/bcrypt"
)
func main() {
 var r struct { Mode, Password, Hash, Secret string }
 if json.NewDecoder(os.Stdin).Decode(&r) != nil { os.Exit(2) }
 if r.Mode == "hash" {
  h,e := bcrypt.GenerateFromPassword([]byte(r.Password),bcrypt.DefaultCost)
  if e != nil { os.Exit(2) }
  json.NewEncoder(os.Stdout).Encode(map[string]string{"hash":string(h)})
  return
 }
 now := time.Unix(1792713600,0).UTC()
 code,e := totp.GenerateCode(r.Secret,now)
 if e != nil { os.Exit(2) }
 ok,e := totp.ValidateCustom(code,r.Secret,now,totp.ValidateOpts{Period:30,Skew:1,Digits:6,Algorithm:0})
 if e != nil || !ok || bcrypt.CompareHashAndPassword([]byte(r.Hash),[]byte(r.Password)) != nil { os.Exit(3) }
 if bcrypt.CompareHashAndPassword([]byte(r.Hash),[]byte(r.Password+"-wrong")) == nil { os.Exit(4) }
 json.NewEncoder(os.Stdout).Encode(map[string]bool{"password_verified":true,"wrong_password_rejected":true,"totp_verified":true})
}
'''


@unittest.skipUnless(OPT_IN, 'Explicit disposable Docker/API restore opt-in required')
class APIRestoreIntegration(unittest.TestCase):
    def setUp(self):
        self.secrets = []
        self.codes = {}
        self.evidence = {'format': 1, 'fixture': 'real-api-native-restore',
                         'production_providers': False, 'cases': [], 'exit_codes': self.codes}
        self.addCleanup(self.report)
        self.evidence['harness_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.adapter = ROOT / 'roles/selfhost_core_data_backup/files/api_backup_receipt.py'
        self.evidence['adapter_sha256'] = hashlib.sha256(self.adapter.read_bytes()).hexdigest()
        self.playbooks_sha = self.run_command('playbooks_head', ['git', 'rev-parse', 'HEAD'], cwd=ROOT).stdout.strip()
        expected_playbooks = os.environ.get('CONTROL_PLANE_PLAYBOOKS_SHA', self.playbooks_sha)
        self.assertEqual(self.playbooks_sha, expected_playbooks, 'Playbooks checkout differs from required full SHA')
        self.assert_playbooks_clean('playbooks_clean')
        self.evidence['playbooks_sha'] = self.playbooks_sha
        self.temporary = tempfile.TemporaryDirectory(prefix='control-plane-api-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.source = Path(os.environ['CONTROL_PLANE_ACCOUNTS_SOURCE']).resolve()
        self.docker = shutil.which('docker')
        self.go = shutil.which('go')
        self.bash = os.environ.get('CONTROL_PLANE_BASH', shutil.which('bash') or '')
        self.assertTrue(self.docker and self.go and self.bash, 'Docker, Go and Bash >=4 required')
        version = self.run_command('bash_version', [self.bash, '-c', 'printf "%s" "$BASH_VERSINFO"']).stdout
        self.assertGreaterEqual(int(version), 4, 'Use CONTROL_PLANE_BASH for Bash >=4')
        endpoint = os.environ.get('DOCKER_HOST')
        if not endpoint:
            endpoint = self.run_command('docker_context', [self.docker, 'context', 'inspect', '--format',
                                        '{{.Endpoints.docker.Host}}']).stdout.strip()
        self.assertTrue(endpoint.startswith('unix://'), 'Only local Unix Docker endpoints permitted')
        # Pin every invocation even if the parent shell changes its context.
        self.docker_env = {'DOCKER_HOST': endpoint}
        image = os.environ['CONTROL_PLANE_POSTGRES_IMAGE']
        self.assertRegex(image, r'^postgres(?:[:][A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$')
        self.image = image
        self.run_command('image_present', [self.docker, 'image', 'inspect', image], env=self.docker_env)
        self.sha = self.run_command('accounts_head', ['git', 'rev-parse', 'HEAD'], cwd=self.source).stdout.strip()
        expected = os.environ.get('CONTROL_PLANE_ACCOUNTS_SHA', self.sha)
        self.assertTrue(self.sha.startswith(expected), 'Accounts checkout differs from required SHA')
        self.assertEqual(self.run_command('accounts_clean', ['git', 'status', '--porcelain'], cwd=self.source).stdout, '',
                         'Accounts source must be clean')
        self.evidence.update(accounts_sha=self.sha, postgres_image=image)
        self.binary = self.root / 'migratectl'
        build_env = {'CGO_ENABLED': '0', 'GOWORK': 'off'}
        self.run_command('build_migratectl', [self.go, 'build', '-o', str(self.binary), './cmd/migratectl'],
                         cwd=self.source, env=build_env, timeout=240)
        self.auth_source = self.root / 'authproof.go'
        self.auth_source.write_text(AUTH_HELPER)
        self.auth_binary = self.root / 'authproof'
        self.run_command('build_authproof', [self.go, 'build', '-o', str(self.auth_binary), str(self.auth_source)],
                         cwd=self.source, env=build_env, timeout=120)
        self.manifest = json.loads(self.cli('native_manifest', ['native-schema']).stdout)
        self.schema_version = str(self.manifest['migration_version'])
        self.evidence['migratectl_sha256'] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.password = self.secret()
        self.totp_secret = base64.b32encode(secrets.token_bytes(20)).decode()
        self.secrets.append(self.totp_secret)
        self.password_hash = json.loads(self.run_command('fixture_bcrypt', [str(self.auth_binary)],
            data=json.dumps({'Mode': 'hash', 'Password': self.password})).stdout)['hash']
        self.secrets.append(self.password_hash)
        self.recovery_hash = hashlib.sha256(self.secret().encode()).hexdigest()
        self.secrets.append(self.recovery_hash)
        self.network = 'cp-api-' + uuid.uuid4().hex[:12]
        # Internal Docker networks suppress host publication on some runtimes.
        # A private bridge without masquerading permits only the explicit
        # loopback DB mapping needed by the native host CLI; no providers run.
        self.run_command('network_create', [self.docker, 'network', 'create', '--driver', 'bridge',
            '--opt', 'com.docker.network.bridge.enable_ip_masquerade=false', self.network], env=self.docker_env)
        self.addCleanup(lambda: self.run_command('network_cleanup', [self.docker, 'network', 'rm', self.network], env=self.docker_env))
        self.source_container, self.source_port, self.source_admin = self.cluster('source')
        self.init_native(self.source_port, self.source_admin, 'source')
        self.seed(self.source_container)
        self.configure_source()
        self.job_id, self.subject = str(uuid.uuid4()), str(uuid.uuid4())
        self.source_key, self.target_key = self.key(), self.key()
        self.source_stage_key, self.target_stage_key = self.key(), self.key()
        self.source_url = 'http://127.0.0.1:' + str(self.free_port())
        self.target_url = 'http://127.0.0.1:' + str(self.free_port())
        self.source_stage = self.private_dir('source-stage')
        self.source_process = self.serve('source', self.source_url, self.source_stage,
                                         self.dsn(self.source_port, 'readonly_release', self.readonly_password))
        self.source_token = self.token('source', 'source.read')
        self.preview_token = self.token('target', 'target.plan')
        self.apply_token = self.token('target', 'target.plan,target.apply')
        self.check_http(self.source_url, None, 401)

    def report(self):
        # Aggregate proof only. No row contents, tokens, passwords or DSNs.
        self.evidence['exit_codes'] = self.codes
        report = os.environ.get('CONTROL_PLANE_API_RESTORE_REPORT')
        if report:
            path = Path(report)
            parent = path.parent.resolve()
            self.assertTrue(path.is_absolute() and parent.is_relative_to(Path('/tmp').resolve()),
                            'Proof output must be under /tmp')
            self.assertFalse(path.exists() or path.is_symlink(), 'Proof file must be new')
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(self.evidence, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
        print('CONTROL_PLANE_API_RESTORE_PROOF=' + json.dumps(self.evidence, sort_keys=True), flush=True)

    def assert_playbooks_clean(self, label):
        # CI intentionally nests these three pinned producer checkouts. Only
        # their untracked directories are allowed; every tracked file and all
        # other untracked paths must match the recorded Playbooks revision.
        status = self.run_command(label, ['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT).stdout
        fixtures = ('.fixture-accounts/', '.fixture-billing/', '.fixture-gitops/')
        unexpected = [line for line in status.splitlines()
                      if not (line.startswith('?? ') and line[3:].startswith(fixtures))]
        self.assertFalse(unexpected, 'Playbooks checkout must be clean except pinned CI fixture checkouts')
        self.evidence['playbooks_clean_except_ci_fixtures'] = True

    def secret(self):
        value = secrets.token_urlsafe(36)
        self.secrets.append(value)
        return value

    def key(self):
        value = base64.b64encode(secrets.token_bytes(32)).decode()
        self.secrets.append(value)
        return value

    def safe(self, value):
        for secret in self.secrets:
            value = value.replace(secret, '<redacted>')
        return value[:1800]

    def run_command(self, label, command, *, data=None, env=None, cwd=None, timeout=90, check=True):
        try:
            result = subprocess.run([str(c) for c in command], input=data, capture_output=True, text=True,
                                    env={**os.environ, **(env or {})}, cwd=cwd, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.codes[label] = 'timeout'
            raise AssertionError(label + ': timeout (output suppressed)') from None
        self.codes[label] = result.returncode
        if check and result.returncode != 0:
            raise AssertionError(f'{label}: exit {result.returncode}: {self.safe(result.stderr)}')
        return result

    def cli(self, label, args, env=None, check=True):
        return self.run_command(label, [self.binary, *args], env=env, check=check, timeout=180)

    def private_dir(self, name):
        path = self.root / name
        path.mkdir(mode=0o700)
        return path

    @staticmethod
    def free_port():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            return probe.getsockname()[1]

    @staticmethod
    def dsn(port, role, password):
        return f'postgres://{role}:{password}@127.0.0.1:{port}/account?sslmode=disable'

    def cluster(self, name):
        container = 'cp-api-' + name + '-' + uuid.uuid4().hex[:12]
        password = self.secret()
        self.run_command(name + '_cluster_create', [self.docker, 'run', '--pull=never', '--rm', '-d',
            '--name', container, '--network', self.network, '--tmpfs', '/var/lib/postgresql/data',
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD', self.image],
            env={**self.docker_env, 'POSTGRES_PASSWORD': password})
        self.addCleanup(lambda: self.run_command(name + '_cluster_cleanup', [self.docker, 'rm', '-f', container], env=self.docker_env))
        probe = (ROOT / 'roles/selfhost_control_plane/files/postgres_final_ready.sh').read_text()
        for _ in range(150):
            ready = self.run_command(name + '_ready', [self.docker, 'exec', '-i', container, 'sh'],
                                     data=probe, env=self.docker_env, check=False)
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            self.fail(name + ': final PostgreSQL process did not become ready')
        port = self.run_command(name + '_port', [self.docker, 'port', container, '5432/tcp'], env=self.docker_env).stdout.strip()
        self.assertRegex(port, r'^127\.0\.0\.1:[0-9]+$')
        self.sql(container, 'postgres', 'CREATE DATABASE account', label=name + '_database_create')
        version = int(self.sql(container, 'account', "SELECT current_setting('server_version_num')", label=name + '_version').stdout)
        self.assertTrue(170000 <= version < 180000, 'PostgreSQL 17 required')
        return container, int(port.split(':')[1]), password

    def sql(self, container, database, sql, *, role='postgres', password=None, label='sql', check=True):
        args = [self.docker, 'exec', '-i']
        env = dict(self.docker_env)
        if password is not None:
            args += ['-e', 'PGPASSWORD']
            env['PGPASSWORD'] = password
        args += [container, 'psql', '-XAtq', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose']
        if password is not None:
            args += ['-h', '127.0.0.1']
        args += ['-U', role, '-d', database]
        return self.run_command(label, args, data=sql, env=env, check=check)

    def init_native(self, port, password, label):
        result = json.loads(self.cli(label + '_native_init', ['init', '--dsn-env', 'FIXTURE_INIT_DSN',
            '--environment', 'sit', '--schema-sha256', self.manifest['schema_sha256'],
            '--writers-paused', '--dry-run=false'], {'FIXTURE_INIT_DSN': self.dsn(port, 'postgres', password)}).stdout)
        self.assertEqual(result['result'], 'initialized')

    def seed(self, container):
        # All identifiers are fixture-only; no external OAuth/payment provider runs.
        self.user = '00000000-0000-0000-0000-000000000101'
        self.proxy = '00000000-0000-0000-0000-000000000201'
        self.subscription = '00000000-0000-0000-0000-000000000301'
        self.invoice = '00000000-0000-0000-0000-000000000401'
        self.payment = '00000000-0000-0000-0000-000000000501'
        self.operation = '00000000-0000-0000-0000-000000000601'
        self.sql(container, 'account', f"""INSERT INTO public.users(uuid,username,password,email,proxy_uuid,mfa_totp_secret,mfa_enabled,email_verified_at)
VALUES('{self.user}','api-fixture','{self.password_hash}','api-fixture@example.invalid','{self.proxy}',
 '{self.totp_secret}',true,'2026-01-01Z');
INSERT INTO public.identities(provider,external_id,user_uuid) VALUES('github','fixture-oauth','{self.user}');
INSERT INTO public.mfa_recovery_codes(id,user_uuid,batch_uuid,code_hash,expires_at)
VALUES(gen_random_uuid(),'{self.user}',gen_random_uuid(),'{self.recovery_hash}','2030-01-01Z');
INSERT INTO public.account_lifecycle_events(user_uuid,to_state,actor_type) VALUES('{self.user}','active','system');
INSERT INTO public.billing_plans(plan_id,display_name) VALUES('fixture-plan','fixture');
INSERT INTO public.subscriptions(uuid,user_uuid,provider,external_id,plan_id)
VALUES('{self.subscription}','{self.user}','local','fixture-sub','fixture-plan');
INSERT INTO public.subscription_records(user_uuid,monthly_quota_bytes) VALUES('{self.user}',9007199254740993);
INSERT INTO public.account_billing_profiles(account_uuid) VALUES('{self.user}');
INSERT INTO public.account_quota_states(account_uuid,remaining_included_quota,current_balance)
VALUES('{self.user}',9007199254740993,12.75);
INSERT INTO public.account_policy_snapshots(account_uuid,policy_version,expires_at) VALUES('{self.user}','fixture','2030-01-01Z');
INSERT INTO public.billing_ledger(id,account_uuid,bucket_start,bucket_end,entry_type,rated_bytes,amount_delta,balance_after)
VALUES(gen_random_uuid(),'{self.user}','2026-01-01Z','2026-01-02Z','usage',9007199254740993,0.125,12.75);
INSERT INTO public.billing_source_sync_state(source_id) VALUES('fixture-source');
INSERT INTO public.traffic_minute_buckets(account_uuid,node_id,bucket_start,total_bytes)
VALUES('{self.user}','fixture-node','2026-01-01Z',9007199254740993);
INSERT INTO public.traffic_stat_checkpoints(account_uuid,node_id,last_seen_at) VALUES('{self.user}','fixture-node','2026-01-01Z');
INSERT INTO public.stripe_webhook_events(event_id,event_type,payload,status)
VALUES('fixture-event','test','{{"n":9007199254740993,"d":1.20}}','processed');
INSERT INTO public.finance_invoices(id,idempotency_key,account_uuid,subscription_uuid,amount_minor,currency)
VALUES('{self.invoice}','fixture-invoice','{self.user}','{self.subscription}',1000,'CNY');
INSERT INTO public.finance_payments(id,idempotency_key,invoice_id,account_uuid,amount_minor,currency)
VALUES('{self.payment}','fixture-payment','{self.invoice}','{self.user}',1000,'CNY');
INSERT INTO public.finance_refunds(idempotency_key,payment_id,amount_minor,currency) VALUES('fixture-refund','{self.payment}',100,'CNY');
INSERT INTO public.finance_operations(id,idempotency_key,operation_type,target_type,target_id,status)
VALUES('{self.operation}','fixture-operation','refund','payment','{self.payment}','succeeded');
INSERT INTO public.finance_operation_events(id,operation_id,attempt,event_type,status)
OVERRIDING SYSTEM VALUE VALUES(99,'{self.operation}',1,'refund','succeeded');
INSERT INTO public.subscription_lifecycle_states(account_uuid,operation_id) VALUES('{self.user}','{self.operation}');
INSERT INTO public.network_teams(id,name,member_limit,valid_from,valid_until) VALUES('fixture-team','fixture',5,'2026-01-01Z','2030-01-01Z');
INSERT INTO public.network_team_members(user_uuid,team_id) VALUES('{self.user}','fixture-team');
INSERT INTO public.team_agent_enrollments(agent_id,assigned_team_id,status) VALUES('fixture-agent','fixture-team','approved');
INSERT INTO public.sessions(token,expires_at,user_uuid) VALUES('{self.secret()}','2030-01-01Z','{self.user}');
INSERT INTO public.oauth_exchange_codes(code,session_token,session_expires_at,expires_at)
VALUES('{self.secret()}','{self.secret()}','2030-01-01Z','2030-01-01Z');
INSERT INTO public.password_recovery_challenges(id,user_uuid,email_snapshot,challenge_kind,secret_hash,expires_at)
VALUES(gen_random_uuid(),'{self.user}','api-fixture@example.invalid','token','{self.recovery_hash}','2030-01-01Z');
""", label='source_seed')

    def configure_source(self):
        self.readonly_password = self.secret()
        self.sql(self.source_container, 'account', f"""
CREATE ROLE readonly_release LOGIN PASSWORD '{self.readonly_password}' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE account TO readonly_release;
GRANT USAGE ON SCHEMA public TO readonly_release;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_release;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO readonly_release;
DO $$DECLARE t record; BEGIN FOR t IN SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relrowsecurity LOOP
 EXECUTE format('CREATE POLICY release_initialization_readonly ON public.%I FOR SELECT TO readonly_release USING(true)',t.relname);
END LOOP; END$$;
""", label='source_readonly_grants')

    def configure_target(self, container):
        self.executor_password, self.app_password = self.secret(), self.secret()
        self.sql(container, 'account', f"""
CREATE ROLE migration_executor LOGIN PASSWORD '{self.executor_password}' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE account_user LOGIN PASSWORD '{self.app_password}' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Database-local CREATE is explicitly provisioned for the API's opt-in
-- CREATE SCHEMA/TABLE receipt metadata. This is not cluster CREATEDB.
GRANT CONNECT,TEMPORARY,CREATE ON DATABASE account TO migration_executor;
GRANT CONNECT ON DATABASE account TO account_user;
GRANT USAGE,CREATE ON SCHEMA public TO migration_executor;
DO $$DECLARE t record; BEGIN FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname<>'schema_migrations' LOOP
 EXECUTE format('ALTER TABLE public.%I OWNER TO migration_executor',t.relname);
END LOOP; END$$;
REVOKE CREATE ON SCHEMA public FROM migration_executor;
GRANT SELECT ON public.schema_migrations TO migration_executor;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO migration_executor;
-- Metadata DDL executes only through --initialize-receipts after real restore.
GRANT USAGE ON SCHEMA public TO account_user;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO account_user;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO account_user;
""", label='target_executor_app_grants')
        self.assertEqual(self.sql(container, 'account', "SELECT rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname='migration_executor'",
                                  label='executor_privileges').stdout.strip(), 'f')

    def token(self, role, scopes):
        result = self.cli(role + '_token_' + scopes.replace(',', '_'), ['migration-token', '--auth-key-env', 'FIXTURE_SIGNING_KEY',
            '--audience', role + '-fixture', '--source-audience', 'source-fixture', '--target-audience', 'target-fixture',
            '--job-id', self.job_id, '--subject', self.subject, '--scopes', scopes, '--ttl', '15m'],
            {'FIXTURE_SIGNING_KEY': self.source_key if role == 'source' else self.target_key})
        value = result.stdout.strip()
        self.secrets.append(value)
        return value

    def serve(self, role, url, stage, dsn, receipt=None):
        args = [self.binary, 'serve', '--role', role, '--audience', role + '-fixture',
            '--source-audience', 'source-fixture', '--target-audience', 'target-fixture', '--job-id', self.job_id,
            '--dsn-env', 'FIXTURE_API_DSN', '--auth-key-env', 'FIXTURE_SIGNING_KEY',
            '--staging-key-env', 'FIXTURE_STAGING_KEY', '--staging-dir', stage,
            '--listen', url.removeprefix('http://'), '--allow-loopback-http']
        if role == 'target':
            args += ['--allowed-source-url', self.source_url, '--allowed-peer-cidr', '127.0.0.1/32']
        if receipt:
            args += ['--backup-receipt-file', receipt, '--initialize-receipts', '--receipt-owner-role', 'migration_executor']
        log = stage.parent / (stage.name + '-' + uuid.uuid4().hex[:8] + '.log')
        with log.open('x') as output:
            os.fchmod(output.fileno(), 0o600)
            process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=output,
                env={**os.environ, 'FIXTURE_API_DSN': dsn, 'FIXTURE_SIGNING_KEY': self.source_key if role == 'source' else self.target_key,
                     'FIXTURE_STAGING_KEY': self.source_stage_key if role == 'source' else self.target_stage_key})
        self.addCleanup(lambda: self.stop(process))
        for _ in range(100):
            if process.poll() is not None:
                self.fail(f'{role} listener exit {process.returncode}: {self.safe(log.read_text())}')
            try:
                self.check_http(url, None, 401, role)
                break
            except (OSError, AssertionError):
                time.sleep(0.1)
        else:
            self.fail(role + ': listener not ready')
        self.codes[role + '_serve_start'] = 0
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def check_http(self, url, token, expected, role='source'):
        path = '/snapshots' if role == 'source' else '/plans/' + str(uuid.uuid4())
        request = urllib.request.Request(url + '/api/migrations/v1' + path,
            data=json.dumps({'request_id': str(uuid.uuid4())}).encode() if role == 'source' else None,
            headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            self.assertEqual(error.headers['Cache-Control'], 'no-store')
            error.close()
        self.assertEqual(status, expected, 'HTTP authorization/readiness status differs')

    def preview(self, label, snapshot_id, plan_id=None):
        args = ['transfer-api', '--source-url', self.source_url, '--target-url', self.target_url,
                '--source-token-env', 'FIXTURE_SOURCE_TOKEN', '--target-token-env', 'FIXTURE_TARGET_TOKEN',
                '--allow-loopback-http', '--snapshot-id', snapshot_id, '--plan-id', plan_id or str(uuid.uuid4())]
        return json.loads(self.cli(label, args, {'FIXTURE_SOURCE_TOKEN': self.source_token,
                                               'FIXTURE_TARGET_TOKEN': self.preview_token}).stdout)

    def apply(self, label, plan, token=None, check=True):
        result = self.cli(label, ['transfer-api', '--target-url', self.target_url, '--target-token-env', 'FIXTURE_TARGET_TOKEN',
            '--allow-loopback-http', '--apply', '--plan-id', plan['plan_id'], '--plan-sha256', plan['plan_sha256']],
            {'FIXTURE_TARGET_TOKEN': token or self.apply_token}, check=check)
        return json.loads(result.stdout) if check else result

    def app_probe(self, container, database, label):
        # A real TCP password-authenticated application connection. Both table
        # inserts occur in one transaction and are rolled back on success/error.
        sql = f"""BEGIN;
SELECT count(*) FROM public.users;
SELECT count(*) FROM public.billing_ledger;
INSERT INTO public.users(uuid,username,password,email,proxy_uuid)
VALUES('00000000-0000-0000-0000-000000009101','grant-probe','{self.password_hash}',
 'grant-probe@example.invalid','00000000-0000-0000-0000-000000009201');
INSERT INTO public.billing_ledger(id,account_uuid,bucket_start,bucket_end,entry_type,rated_bytes,amount_delta,balance_after)
VALUES('00000000-0000-0000-0000-000000009301','00000000-0000-0000-0000-000000009101',
 '2026-01-01Z','2026-01-02Z','usage',123,0.125,12.75);
ROLLBACK;"""
        result = self.sql(container, database, sql, role='account_user', password=self.app_password, label=label, check=False)
        # Never expose driver messages or SQL containing fixture credentials.
        return {'exit_code': result.returncode, 'permission_denied': '42501' in result.stderr,
                'users_permission_denied': 'permission denied for table users' in result.stderr,
                'billing_permission_denied': 'permission denied for table billing_ledger' in result.stderr}

    def backup_restore(self, container, plan, case):
        directory = self.private_dir(case + '-backup')
        shim_dir = directory / 'bin'
        shim_dir.mkdir(mode=0o700)
        # Reject every container other than the script's fixed logical target;
        # preserve all SQL/dump flags and bind Docker to this local Unix socket.
        shim = shim_dir / 'docker'
        shim.write_text('#!' + self.bash + '\nset -euo pipefail\n'
            'args=("$@")\nfound=0\nfor i in "${!args[@]}"; do\n'
            ' if [[ "${args[$i]}" == web-saas-postgresql ]]; then args[$i]=' + shlex.quote(container) + '; found=1; fi\ndone\n'
            '[[ "$found" == 1 && "${args[0]}" == exec ]] || exit 91\n'
            'export DOCKER_HOST=' + shlex.quote(self.docker_env['DOCKER_HOST']) + '\nexec ' + shlex.quote(self.docker) + ' "${args[@]}"\n')
        shim.chmod(0o700)
        # Linux sync -f is unavailable on macOS. fsync the actual archive/dir,
        # without weakening durability or changing any database operation.
        sync = shim_dir / 'sync'
        sync.write_text('#!' + sys.executable + '\nimport os,sys\n'
            'assert len(sys.argv)==3 and sys.argv[1]=="-f"\n'
            'fd=os.open(sys.argv[2],os.O_RDONLY)\n'
            'try: os.fsync(fd)\nfinally: os.close(fd)\n')
        sync.chmod(0o700)
        backup_root = directory / 'backups'
        run_id = str(secrets.randbelow(900000000000) + 100000000000)
        destination = backup_root / 'sit' / 'fixture' / run_id
        destination.mkdir(parents=True, mode=0o700)
        scripts = {}
        for role, name in (('web_saas_data_backup', 'create_encrypted_backup.sh'),
                           ('web_saas_data_restore_verify', 'restore_verify.sh')):
            path = directory / name
            body = (ROOT / 'roles' / role / 'files' / name).read_text()
            self.evidence.setdefault('scripts_sha256', {})[name] = hashlib.sha256(body.encode()).hexdigest()
            path.write_text(body.replace('/data/backups/web-saas', str(backup_root)))
            scripts[name] = path
        env = {**self.docker_env, 'PATH': str(shim_dir) + os.pathsep + os.environ['PATH'],
               'WEB_SAAS_DATABASE': 'account', 'WEB_SAAS_POSTGRES_CONTAINER': 'web-saas-postgresql',
               'WEB_SAAS_ENVIRONMENT': 'sit', 'WEB_SAAS_EXPECTED_VERSION': self.schema_version,
               'WEB_SAAS_SOURCE_DATABASE_ID': 'fixture-target-' + case, 'WEB_SAAS_BASELINE_ID': plan['plan_id'],
               'WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID': 'fixture-subscription',
               'WEB_SAAS_BACKUP_KEY_STDIN': 'true', 'WEB_SAAS_FULL_DATABASE': 'true',
               'WEB_SAAS_ALLOW_EMPTY_USERS': 'true', 'WEB_SAAS_RUN_ID': run_id,
               'WEB_SAAS_BACKUP_ROOT': str(destination), 'WEB_SAAS_DIAGNOSTIC_PATH': str(directory / 'diagnostic')}
        passphrase = self.secret() + '\n'
        backup = json.loads(self.run_command(case + '_encrypted_backup', [self.bash, scripts['create_encrypted_backup.sh']],
                                             data=passphrase, env=env, timeout=180).stdout)
        self.assertTrue(backup['encrypted'] and backup['durable'] and backup['full_database'])
        archive = Path(backup['archive_path'])
        self.assertNotIn(self.password_hash.encode(), archive.read_bytes())
        restore_env = {**env, 'WEB_SAAS_ARCHIVE_PATH': backup['archive_path'],
            'WEB_SAAS_ARCHIVE_SHA256': backup['archive_sha256'],
            'WEB_SAAS_EXPECTED_USERS': str(backup['existing_users']),
            'WEB_SAAS_EXPECTED_SUBSCRIPTIONS': str(backup['subscriptions']),
            'WEB_SAAS_EXPECTED_SCHEMA_SHA256': backup['schema_sha256'],
            'WEB_SAAS_EXPECTED_DATA_SHA256': backup['data_sha256'],
            'WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER': backup['database_system_identifier'],
            'WEB_SAAS_RETAIN_RESTORE': 'true'}
        # This documented flag retains the actual verified new DB for app probes.
        # It never supplies a verification result; all checks run in the script.
        restore = json.loads(self.run_command(case + '_isolated_restore', [self.bash, scripts['restore_verify.sh']],
                                              data=passphrase, env=restore_env, timeout=180).stdout)
        self.assertTrue(restore['isolated_restore_verified'] and restore['restored_data_matches'])
        self.assertTrue(restore['source_restore_schema_matches'] and restore['restore_retained'])
        return backup, restore, env, directory

    def fingerprint(self, container, label):
        # Includes rows, xmin versions, receipt data and sequence state. Exact
        # retry must cause no writes, even writes of identical business content.
        commands = self.sql(container, 'account', """SELECT statement FROM (
SELECT format('SELECT %L; SELECT to_jsonb(t)::text,xmin::text FROM %I.%I t ORDER BY to_jsonb(t)::text COLLATE "C";',
 n.nspname||'.'||c.relname,n.nspname,c.relname) statement FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('public','accounts_migration_api') AND c.relkind='r'
UNION ALL SELECT format('SELECT %L; SELECT last_value,is_called FROM %I.%I;',n.nspname||'.'||c.relname,n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'
) q ORDER BY statement COLLATE "C";
""", label=label + '_queries').stdout
        body = self.sql(container, 'account', "SET timezone='UTC';\n" + commands, label=label).stdout
        return hashlib.sha256(body.encode()).hexdigest()

    def verify_business(self, container, label, populated):
        restored = json.loads(self.sql(container, 'account',
            f"SELECT row_to_json(t) FROM (SELECT uuid,proxy_uuid,password,mfa_totp_secret,mfa_enabled,email_verified FROM public.users WHERE uuid='{self.user}') t",
            label=label + '_identity').stdout)
        self.assertEqual(restored['uuid'], self.user)
        self.assertEqual(restored['proxy_uuid'], self.proxy)
        self.assertTrue(restored['password'] == self.password_hash, 'Password verifier was changed')
        self.assertTrue(restored['mfa_totp_secret'] == self.totp_secret, 'MFA secret was changed')
        self.assertTrue(restored['mfa_enabled'] and restored['email_verified'])
        auth = json.loads(self.run_command(label + '_password_totp', [self.auth_binary], data=json.dumps({
            'Mode': 'verify', 'Password': self.password, 'Hash': restored['password'], 'Secret': restored['mfa_totp_secret']})).stdout)
        self.assertTrue(all(auth.values()))
        invariants = self.sql(container, 'account', f"""SELECT
 (SELECT count(*) FROM public.identities WHERE provider='github' AND external_id='fixture-oauth' AND user_uuid='{self.user}')=1
 AND (SELECT count(*) FROM public.mfa_recovery_codes WHERE user_uuid='{self.user}' AND code_hash='{self.recovery_hash}' AND consumed_at IS NULL)=1
 AND (SELECT remaining_included_quota::text||':'||current_balance::text FROM public.account_quota_states WHERE account_uuid='{self.user}')='9007199254740993:12.75'
 AND (SELECT monthly_quota_bytes FROM public.subscription_records WHERE user_uuid='{self.user}')=9007199254740993
 AND (SELECT amount_minor FROM public.finance_payments WHERE id='{self.payment}')=1000
 AND (SELECT amount_minor FROM public.finance_refunds WHERE payment_id='{self.payment}')=100
 AND (SELECT count(*) FROM public.sessions)=0
 AND (SELECT count(*) FROM public.oauth_exchange_codes)=0
 AND (SELECT count(*) FROM public.password_recovery_challenges)=0
 AND (SELECT count(*) FROM public.users)={2 if populated else 1}
 AND (SELECT count(*) FROM public.finance_operation_events WHERE id=99)=1;""", label=label + '_invariants').stdout.strip()
        self.assertEqual(invariants, 't', 'Identity/MFA/finance/session exclusion invariant failed')
        return auth

    def test_real_restore_gate_apply_retry_and_restored_application_permissions(self):
        permissions_failures = []
        for populated in (False, True):
            case = 'populated' if populated else 'empty'
            with self.subTest(target=case):
                container, port, admin = self.cluster(case)
                self.init_native(port, admin, case)
                self.configure_target(container)
                if populated:
                    self.sql(container, 'account', """
INSERT INTO public.users(uuid,username,password,email,proxy_uuid)
VALUES('00000000-0000-0000-0000-000000000103','target-only','target-fixture-hash','target-only@example.invalid','00000000-0000-0000-0000-000000000203');
INSERT INTO public.billing_plans(plan_id,display_name) VALUES('target-only-plan','Target sentinel');
INSERT INTO public.subscriptions(uuid,user_uuid,provider,external_id,plan_id)
VALUES('00000000-0000-0000-0000-000000000303','00000000-0000-0000-0000-000000000103','local','target-only-sub','target-only-plan');
INSERT INTO public.account_quota_states(account_uuid,remaining_included_quota,current_balance)
VALUES('00000000-0000-0000-0000-000000000103',456789,44.125);
""", label='target_sentinel_seed')
                before_probe = self.fingerprint(container, case + '_before_app_probe')
                original_app = self.app_probe(container, 'account', case + '_original_app_probe')
                self.assertEqual(original_app['exit_code'], 0, 'Original target app grants must work')
                self.assertEqual(before_probe, self.fingerprint(container, case + '_after_app_probe'), 'Application probe must roll back')
                stage = self.private_dir(case + '-target-stage')
                target_dsn = self.dsn(port, 'migration_executor', self.executor_password)
                target = self.serve('target', self.target_url, stage, target_dsn)
                baseline = self.fingerprint(container, case + '_before_preview')
                snapshot_id = str(uuid.uuid4())
                plan = self.preview(case + '_preview', snapshot_id)
                self.assertEqual(plan['state'], 'preview')
                self.assertGreater(plan['insert_rows'], 0)
                self.assertEqual(plan['conflict_rows'], 0)
                self.assertEqual(baseline, self.fingerprint(container, case + '_after_preview'), 'Preview changed target')
                self.assertNotEqual(self.apply(case + '_no_apply_scope', plan, self.preview_token, False).returncode, 0)
                self.assertNotEqual(self.apply(case + '_no_backup_gate', plan, check=False).returncode, 0)
                backup, restore, env, directory = self.backup_restore(container, plan, case)
                self.assertEqual(backup['existing_users'], 1 if populated else 0)
                self.assertEqual(backup['subscriptions'], 1 if populated else 0)
                self.assertEqual(restore['g3_status'], 'passed' if populated else 'blocked')
                restored_app = self.app_probe(container, restore['restore_database'], case + '_restored_app_probe')
                # Diagnose each table independently even if the first SELECT
                # aborts the combined transaction. No grants are applied here.
                for table in ('users', 'billing_ledger'):
                    insert = ("INSERT INTO public.users(uuid,username,password,email,proxy_uuid) "
                        "VALUES('00000000-0000-0000-0000-000000009101','grant-probe','unused-probe',"
                        "'grant-probe@example.invalid','00000000-0000-0000-0000-000000009201');"
                        if table == 'users' else
                        "INSERT INTO public.billing_ledger(id,account_uuid,bucket_start,bucket_end,entry_type,rated_bytes,amount_delta,balance_after) "
                        "SELECT '00000000-0000-0000-0000-000000009301'::uuid,'00000000-0000-0000-0000-000000009101'::uuid,"
                        "'2026-01-01Z'::timestamptz,'2026-01-02Z'::timestamptz,'usage',123,0.125,12.75 WHERE false;")
                    for action, statement in (
                        ('select', f'SELECT count(*) FROM public.{table};'),
                        ('insert', insert)):
                        probe = self.sql(container, restore['restore_database'], 'BEGIN;\n' + statement + '\nROLLBACK;',
                            role='account_user', password=self.app_password,
                            label=case + '_restored_' + table + '_' + action, check=False)
                        restored_app[table + '_' + action] = {'exit_code': probe.returncode, 'sqlstate_42501': '42501' in probe.stderr}
                case_proof = {'target': case, 'original_app': original_app, 'restored_app': restored_app,
                    'backup_exit': self.codes[case + '_encrypted_backup'], 'restore_exit': self.codes[case + '_isolated_restore'],
                    'archive_sha256': backup['archive_sha256'], 'native_schema_sha256': backup['schema_sha256'],
                    'native_data_sha256': backup['data_sha256'], 'existing_users': backup['existing_users'],
                    'subscriptions': backup['subscriptions'], 'native_g3_status': restore['g3_status'],
                    'restored_rows_match': restore['restored_data_matches'], 'restore_permission_accepted': restored_app['exit_code'] == 0}
                self.evidence['cases'].append(case_proof)
                if restored_app['exit_code'] != 0:
                    permissions_failures.append(case)
                    case_proof['apply'] = 'NOT_RUN_RESTORED_APPLICATION_PERMISSIONS_FAILED'
                    # Never issue a usable proof or apply after a failed real
                    # application restore gate. Fail closed, preserve evidence.
                    self.stop(target)
                    continue
                fresh = self.preview(case + '_fresh_preview', snapshot_id)
                request = {'backup': backup, 'restore': restore, 'plan': plan, 'fresh_plan': fresh,
                    'plan_id': plan['plan_id'], 'plan_sha256': plan['plan_sha256'],
                    'owner_sha': self.evidence['playbooks_sha'], 'receipt_file': str(directory / 'receipt.json'),
                    'proof_file': str(directory / 'proof.json')}
                self.assertEqual(hashlib.sha256(self.adapter.read_bytes()).hexdigest(), self.evidence['adapter_sha256'],
                                 'Adapter changed after setup; refusing mixed-revision evidence')
                proof = json.loads(self.run_command(case + '_real_adapter', [sys.executable, self.adapter],
                    data=json.dumps(request), env=env).stdout)
                self.assertTrue(proof['backup_gate_issued'])
                self.assertEqual(proof['proof']['populated_restore'], populated)
                self.stop(target)
                target = self.serve('target', self.target_url, stage, target_dsn, Path(request['receipt_file']))
                applied = self.apply(case + '_apply', plan)
                self.assertEqual(applied['state'], 'applied')
                case_proof['auth'] = self.verify_business(container, case, populated)
                if populated:
                    sentinel = self.sql(container, 'account', """SELECT
 (SELECT password FROM public.users WHERE username='target-only')='target-fixture-hash'
 AND (SELECT current_balance FROM public.account_quota_states WHERE account_uuid='00000000-0000-0000-0000-000000000103')=44.125
 AND (SELECT remaining_included_quota FROM public.account_quota_states WHERE account_uuid='00000000-0000-0000-0000-000000000103')=456789
 AND (SELECT count(*) FROM public.subscriptions WHERE external_id='target-only-sub')=1""", label='target_only_preserved').stdout.strip()
                    self.assertEqual(sentinel, 't')
                after_apply = self.fingerprint(container, case + '_after_apply')
                retry = self.apply(case + '_exact_retry', plan)
                self.assertEqual(retry, applied)
                self.assertEqual(after_apply, self.fingerprint(container, case + '_after_retry'), 'Exact retry performed writes')
                self.assertEqual(self.sql(container, 'account', 'SELECT count(*) FROM accounts_migration_api.receipts', label=case + '_receipt_count').stdout.strip(), '1')
                case_proof.update(apply='passed', exact_retry_no_writes=True, plan_sha256=plan['plan_sha256'])
                self.stop(target)
        self.assertFalse(permissions_failures,
            'Restored account_user SELECT/INSERT permissions lost for targets: ' + ', '.join(permissions_failures)
            + '; native data/schema restore passed but application acceptance failed; adapter/apply not run')
        self.assertEqual(self.run_command('playbooks_head_after', ['git', 'rev-parse', 'HEAD'], cwd=ROOT).stdout.strip(),
                         self.playbooks_sha, 'Playbooks revision changed during execution')
        self.assert_playbooks_clean('playbooks_clean_after')
        self.assertEqual(hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), self.evidence['harness_sha256'],
                         'Harness changed during execution')


if __name__ == '__main__':
    unittest.main()
