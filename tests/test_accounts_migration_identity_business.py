import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import uuid
import os
from unittest.mock import patch

import yaml


PATH = Path(__file__).resolve().parents[1] / 'roles/accounts_migration_identity/files/bootstrap.py'
spec = importlib.util.spec_from_file_location('accounts_migration_identity', PATH)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def declaration():
    return {
        'schema_version': 1, 'kind': 'accounts-migration-bootstrap', 'role': 'target', 'target_mode': 'business',
        'schema_mode': 'unversioned_verified_scope', 'namespace': 'migration-fixture',
        'source_namespace': 'postgres-fixture', 'postgres_pod': 'postgres-fixture-0',
        'postgres_admin': 'postgres', 'database_host': 'postgres.fixture.svc', 'database_name': 'account',
        'tls_namespace': 'ingress-fixture', 'tls_secret': 'fixture-tls',
        'vault_path': 'kv/test/migration/target', 'staging_key_vault_path': 'kv/test/migration/staging',
        'listener_secret_name': 'migration-listener-business', 'native_job_path': None,
        'tables': [f'fixture_{chr(97+i//26)}{chr(97+i%26)}' for i in range(42)],
    }


class BusinessMigrationIdentityTest(unittest.TestCase):
    def test_explicit_staged_target_contract(self):
        c = declaration()
        self.assertEqual(bootstrap.target_options(c), ('business', 'migration-listener-business'))
        for change in (
            {'listener_secret_name': 'migration-listener'}, {'staging_key_vault_path': c['vault_path']},
            {'staging_key_vault_path': '../bad'}, {'schema_mode': 'native'}, {'role': 'source'},
            {'tables': c['tables'][:-1]}, {'tables': [c['tables'][0]] * 42},
        ):
            invalid = dict(c, **change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                bootstrap.target_options(invalid)
        self.assertEqual(bootstrap.target_options({'role': 'target'}), ('candidate', 'migration-listener'))

    def test_login_requires_wrong_password_denial_and_nonloopback_client(self):
        c, password = declaration(), 'b' * 64
        good = subprocess.CompletedProcess([], 0, stdout='accounts_migration|10.52.0.12\n', stderr='')
        bad = subprocess.CompletedProcess([], 1, stdout='', stderr='')
        with patch.object(bootstrap.subprocess, 'run', side_effect=[bad, good]):
            bootstrap.verify_business_login('fixture-host', c, password)
        for replies in ([good], [bad, bad], [bad, subprocess.CompletedProcess([], 0, stdout='accounts_migration|127.0.0.1\n', stderr='')], [bad, subprocess.CompletedProcess([], 0, stdout='accounts_migration|::ffff:127.0.0.1\n', stderr='')], [bad, subprocess.CompletedProcess([], 0, stdout='other|10.52.0.12\n', stderr='')]):
            with self.subTest(replies=replies), patch.object(bootstrap.subprocess, 'run', side_effect=replies), self.assertRaises(RuntimeError):
                bootstrap.verify_business_login('fixture-host', c, password)

    def test_permissions_are_scoped_and_never_touch_source(self):
        c = declaration()
        sha = 'a' * 40
        secret = 'b' * 64
        key = base64.b64encode(b'synthetic-staging-key-32-bytes!!').decode()
        self.assertEqual(len(base64.b64decode(key)), 32)
        sql_calls = []

        def fake_run(args, body=None):
            if args[0] == 'git':
                return sha if args[-1] == 'HEAD' else ''
            if args[0] == 'vault':
                return json.dumps({'data': {'data': {'staging_key': key}}})
            if args[0] == 'ssh':
                self.assertIn('postgres-fixture', args[-1])
                if body.startswith('BEGIN;'):
                    sql_calls.append(body)
                    return ''
                if "to_regclass('public.schema_migrations')" in body:
                    return 'f'
                return '0'
            raise AssertionError('unexpected command')

        vault = subprocess.CompletedProcess([], 0, stdout=json.dumps({'data': {'data': {'database_password': secret}}}), stderr='')

        def fake_process(args, input=None, **kwargs):
            if args[0] == 'vault':
                return vault
            if args[0] == 'ssh':
                return subprocess.CompletedProcess(args, 0 if input == secret + '\n' else 1, stdout='accounts_migration|10.52.0.12\n' if input == secret + '\n' else '', stderr='')
            raise AssertionError('unexpected subprocess')

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'operation.yaml').write_text(yaml.safe_dump(c), encoding='utf-8')
            argv = ['bootstrap.py', '--gitops', directory, '--gitops-sha', sha, '--operation', 'operation.yaml', '--host', 'fixture-host', '--phase', 'permissions']
            out = io.StringIO()
            with patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap.subprocess, 'run', side_effect=fake_process), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out):
                bootstrap.main()
            self.assertEqual(len(sql_calls), 1)
            grants = [line for line in sql_calls[0].splitlines() if 'GRANT SELECT, INSERT, UPDATE' in line]
            self.assertEqual(len(grants), 42)
            self.assertTrue(all('DELETE' not in line and 'TRUNCATE' not in line for line in grants))
            self.assertNotIn('ALTER ROLE accounts_migration NOINHERIT PASSWORD', sql_calls[0])
            self.assertIn("pg_get_expr(p.polwithcheck,p.polrelid)='true'", sql_calls[0])
            self.assertNotIn(secret, out.getvalue())
            self.assertNotIn(key, out.getvalue())
            self.assertEqual(json.loads(out.getvalue())['result'], 'completed')

    def test_secrets_are_staged_without_replacing_active_listener(self):
        c = declaration()
        sha, secret = 'a' * 40, 'b' * 64
        key = base64.b64encode(b'synthetic-staging-key-32-bytes!!').decode()
        applied = []

        def fake_run(args, body=None):
            if args[0] == 'git':
                return sha if args[-1] == 'HEAD' else ''
            if args[0] == 'vault':
                return json.dumps({'data': {'data': {'staging_key': key}}})
            if args[0] == 'ssh' and 'get secret' in args[-1]:
                return json.dumps({'data': {'tls.crt': base64.b64encode(b'fixture-cert').decode(), 'tls.key': base64.b64encode(b'fixture-key').decode()}})
            if args[0] == 'ssh' and body:
                applied.append(json.loads(body))
                return ''
            raise AssertionError('unexpected command')

        vault = subprocess.CompletedProcess([], 0, stdout=json.dumps({'data': {'data': {'database_password': secret}}}), stderr='')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'operation.yaml').write_text(yaml.safe_dump(c), encoding='utf-8')
            argv = ['bootstrap.py', '--gitops', directory, '--gitops-sha', sha, '--operation', 'operation.yaml', '--host', 'fixture-host', '--phase', 'secrets']
            out = io.StringIO()
            with patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap.subprocess, 'run', return_value=vault), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out):
                bootstrap.main()
        names = {item['metadata']['name'] for item in applied}
        self.assertEqual(names, {'migration-listener-business'})
        target = next(item for item in applied if item['metadata']['name'] == 'migration-listener-business')
        self.assertEqual(base64.b64decode(target['data']['STAGING_KEY']).decode(), key)
        self.assertIn(c['database_host'], base64.b64decode(target['data']['MIGRATION_DSN']).decode())
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn(key, out.getvalue())

    def test_existing_role_wrong_password_stops_before_grants(self):
        c = declaration()
        sha, secret = 'a' * 40, 'b' * 64
        key = base64.b64encode(b'synthetic-staging-key-32-bytes!!').decode()
        writes = []

        def fake_run(args, body=None):
            if args[0] == 'git':
                return sha if args[-1] == 'HEAD' else ''
            if args[0] == 'vault':
                return json.dumps({'data': {'data': {'staging_key': key}}})
            if args[0] == 'ssh':
                if body.startswith('BEGIN;'):
                    writes.append(body)
                    return ''
                if body == "SELECT count(*) FROM pg_roles WHERE rolname='accounts_migration';":
                    return '1'
                if "to_regclass('public.schema_migrations')" in body:
                    return 'f'
                return '0'
            raise AssertionError('unexpected command')

        def fake_process(args, **kwargs):
            if args[0] == 'vault':
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'data': {'data': {'database_password': secret}}}), stderr='')
            return subprocess.CompletedProcess(args, 1, stdout='', stderr='authentication denied')

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'operation.yaml').write_text(yaml.safe_dump(c), encoding='utf-8')
            argv = ['bootstrap.py', '--gitops', directory, '--gitops-sha', sha, '--operation', 'operation.yaml', '--host', 'fixture-host', '--phase', 'permissions']
            with patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap.subprocess, 'run', side_effect=fake_process), patch.object(sys, 'argv', argv), self.assertRaisesRegex(RuntimeError, 'cannot authenticate'):
                bootstrap.main()
        self.assertEqual(writes, [])

    def test_new_business_vault_path_is_created_with_cas(self):
        c, sha = declaration(), 'a' * 40
        key = base64.b64encode(b'synthetic-staging-key-32-bytes!!').decode()
        writes, applied = [], []

        def fake_run(args, body=None):
            if args[0] == 'git':
                return sha if args[-1] == 'HEAD' else ''
            if args[:3] == ['vault', 'kv', 'put']:
                writes.append((args, json.loads(body)))
                return ''
            if args[0] == 'vault':
                return json.dumps({'data': {'data': {'staging_key': key}}})
            if args[0] == 'ssh' and 'get secret' in args[-1]:
                return json.dumps({'data': {'tls.crt': base64.b64encode(b'cert').decode(), 'tls.key': base64.b64encode(b'key').decode()}})
            if args[0] == 'ssh' and body:
                applied.append(json.loads(body))
                return ''
            raise AssertionError('unexpected command')

        missing = subprocess.CompletedProcess([], 1, stdout='', stderr='No value found')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'operation.yaml').write_text(yaml.safe_dump(c), encoding='utf-8')
            argv = ['bootstrap.py', '--gitops', directory, '--gitops-sha', sha, '--operation', 'operation.yaml', '--host', 'fixture-host', '--phase', 'secrets']
            out = io.StringIO()
            with patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap.subprocess, 'run', return_value=missing), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out):
                bootstrap.main()
        self.assertEqual(len(writes), 1)
        self.assertIn('-cas=0', writes[0][0])
        self.assertEqual(set(writes[0][1]), {'database_password'})
        self.assertEqual(applied[0]['metadata']['name'], 'migration-listener-business')
        self.assertNotIn(writes[0][1]['database_password'], out.getvalue())

    def test_business_permissions_against_disposable_postgres(self):
        dsn = os.environ.get('ACCOUNTS_IDENTITY_TEST_DSN')
        if not dsn:
            self.skipTest('dedicated loopback PostgreSQL fixture not configured')
        parsed = urllib.parse.urlparse(dsn)
        if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username != 'postgres' or parsed.path != '/postgres' or parsed.password:
            self.fail('requires dedicated loopback postgres fixture without inline credentials')
        real_run = subprocess.run
        base = ['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-h', parsed.hostname, '-p', str(parsed.port or 5432), '-U', 'postgres', '-At']
        name = 'identity_fixture_' + uuid.uuid4().hex
        c, sha, password = declaration(), 'a' * 40, 'b' * 64
        c['database_name'] = name
        stage = base64.b64encode(b'synthetic-staging-key-32-bytes!!').decode()

        def psql(database, query):
            result = real_run(base + ['-d', database], input=query, text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError('isolated PostgreSQL operation failed')
            return result.stdout.strip()

        if psql('postgres', "SELECT count(*) FROM pg_roles WHERE rolname='accounts_migration';") != '0':
            self.fail('fixture role already exists; refusing to modify it')
        psql('postgres', 'CREATE DATABASE "' + name + '";')
        try:
            setup = ''.join('CREATE TABLE public."' + table + '"(id integer); ALTER TABLE public."' + table + '" ENABLE ROW LEVEL SECURITY;\n' for table in c['tables'])
            psql(name, setup)

            def fake_run(args, body=None):
                if args[0] == 'git':
                    return sha if args[-1] == 'HEAD' else ''
                if args[0] == 'vault':
                    return json.dumps({'data': {'data': {'staging_key': stage}}})
                if args[0] == 'ssh':
                    return psql(name, body)
                raise AssertionError('unexpected command')

            vault = subprocess.CompletedProcess([], 0, stdout=json.dumps({'data': {'data': {'database_password': password}}}), stderr='')
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, 'operation.yaml').write_text(yaml.safe_dump(c), encoding='utf-8')
                argv = ['bootstrap.py', '--gitops', directory, '--gitops-sha', sha, '--operation', 'operation.yaml', '--host', 'fixture-host', '--phase', 'permissions']
                with patch.object(bootstrap, 'run', side_effect=fake_run), patch.object(bootstrap.subprocess, 'run', return_value=vault), patch.object(bootstrap, 'verify_business_login'), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                    bootstrap.main()
                    before = psql('postgres', "SELECT md5(rolpassword) FROM pg_authid WHERE rolname='accounts_migration';")
                    bootstrap.main()
                    after = psql('postgres', "SELECT md5(rolpassword) FROM pg_authid WHERE rolname='accounts_migration';")
                    self.assertEqual(before, after)
                    first = c['tables'][0]
                    psql(name, 'ALTER POLICY migration_candidate_access ON public."' + first + '" USING (false) WITH CHECK (false);')
                    with self.assertRaises(RuntimeError):
                        bootstrap.main()
            report = psql(name, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND has_table_privilege('accounts_migration',c.oid,'SELECT') AND has_table_privilege('accounts_migration',c.oid,'INSERT') AND has_table_privilege('accounts_migration',c.oid,'UPDATE') AND NOT has_table_privilege('accounts_migration',c.oid,'DELETE') AND c.relowner<>(SELECT oid FROM pg_roles WHERE rolname='accounts_migration');")
            self.assertEqual(report, '42')
        finally:
            psql('postgres', 'DROP DATABASE "' + name + '" WITH (FORCE);')
            psql('postgres', 'DROP ROLE IF EXISTS accounts_migration;')


if __name__ == '__main__':
    unittest.main()
