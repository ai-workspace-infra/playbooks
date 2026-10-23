"""Opt-in, real local PostgreSQL 17 encrypted full backup and restore fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('CONTROL_PLANE_POSTGRES_FIXTURE') == '1', 'Explicit local disposable Docker opt-in required')
class PostgresRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which('docker')
        endpoint = subprocess.check_output([cls.docker, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
        if not endpoint.startswith('unix://'):
            raise RuntimeError('Fixture refuses a remote Docker endpoint')
        cls.container = subprocess.check_output([cls.docker, 'run', '--pull=never', '--rm', '-d', '--network', 'none',
            '--name', 'control-plane-fixture-' + uuid.uuid4().hex[:12], '--tmpfs', '/var/lib/postgresql/data',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', os.environ.get('CONTROL_PLANE_POSTGRES_IMAGE', 'postgres:17')], text=True).strip()
        cls.addClassCleanup(lambda: subprocess.run([cls.docker, 'rm', '-f', cls.container], capture_output=True, check=True))
        for _ in range(100):
            # The official image's initialization server exposes only a Unix
            # socket and exits before the final server starts. Require TCP to
            # avoid accepting that temporary server as fixture readiness.
            if subprocess.run([cls.docker, 'exec', cls.container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], capture_output=True).returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Disposable PostgreSQL fixture failed readiness')

    @classmethod
    def q(cls, database, sql):
        result = subprocess.run([cls.docker, 'exec', '-i', cls.container, 'psql', '-U', 'postgres', '-d', database,
            '-XAtq', '-v', 'ON_ERROR_STOP=1'], input=sql, text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError('Disposable fixture SQL failed: ' + result.stderr)
        return result.stdout.strip()

    def test_populated_and_empty_full_database_round_trip_and_tamper_refusal(self):
        self.q('postgres', 'CREATE DATABASE account')
        self.q('account', 'CREATE TABLE schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL); '
            'INSERT INTO schema_migrations VALUES(2026102002,false); CREATE TABLE users(id bigserial PRIMARY KEY, fact text); '
            "INSERT INTO users(fact) VALUES('private-fixture-marker'); CREATE TABLE subscriptions(id bigserial PRIMARY KEY, user_id bigint); "
            'INSERT INTO subscriptions(user_id) VALUES(1); CREATE SCHEMA extra_domain; '
            "CREATE FUNCTION extra_domain.marker() RETURNS text LANGUAGE sql AS $$SELECT 'fixture'::text$$;")
        with tempfile.TemporaryDirectory(prefix='control-plane-pg-') as temporary:
            root = Path(temporary).resolve()
            shim = root / 'docker'
            shim.write_text('#!/bin/bash\nset -euo pipefail\nargs=("$@")\nfor i in "${!args[@]}"; do\n'
                ' if [[ "${args[$i]}" == web-saas-postgresql ]]; then args[$i]="' + self.container + '"; fi\n'
                'done\nexec "' + self.docker + '" "${args[@]}"\n')
            shim.chmod(0o700)
            # Only the backup ROOT is relocated to the isolated fixture directory.
            # SQL, dump/restore flags, encryption and verification execute unchanged.
            backup_root = root / 'backups'
            scripts = {}
            for role, name in (('web_saas_data_backup', 'create_encrypted_backup.sh'), ('web_saas_data_restore_verify', 'restore_verify.sh')):
                output = root / name
                output.write_text((ROOT / 'roles' / role / 'files' / name).read_text().replace('/data/backups/web-saas', str(backup_root)))
                scripts[name] = output
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'WEB_SAAS_DATABASE': 'account',
                'WEB_SAAS_POSTGRES_CONTAINER': 'web-saas-postgresql', 'WEB_SAAS_ENVIRONMENT': 'uat',
                'WEB_SAAS_EXPECTED_VERSION': '2026102002', 'WEB_SAAS_SOURCE_DATABASE_ID': 'fixture-target',
                'WEB_SAAS_BASELINE_ID': 'fixture-baseline', 'WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID': 'fixture-sample',
                'WEB_SAAS_BACKUP_KEY_STDIN': 'true', 'WEB_SAAS_FULL_DATABASE': 'true', 'WEB_SAAS_ALLOW_EMPTY_USERS': 'true'}
            def script(name, environment):
                return subprocess.run(['bash', str(scripts[name])], input='fixture-only-encryption-passphrase-32-characters\n',
                    env=environment, capture_output=True, text=True, timeout=90)
            for run_id in ('98765432101', '98765432102'):
                destination = backup_root / 'uat' / 'fixture' / run_id
                destination.mkdir(parents=True, mode=0o700)
                env.update(WEB_SAAS_RUN_ID=run_id, WEB_SAAS_BACKUP_ROOT=str(destination))
                backed = script('create_encrypted_backup.sh', env)
                self.assertEqual(backed.returncode, 0, backed.stderr)
                receipt = json.loads(backed.stdout)
                archive = Path(receipt['archive_path'])
                self.assertNotIn(b'private-fixture-marker', archive.read_bytes())
                self.assertTrue(receipt['full_database'])
                restore_env = {**env, 'WEB_SAAS_ARCHIVE_PATH': str(archive), 'WEB_SAAS_ARCHIVE_SHA256': receipt['archive_sha256'],
                    'WEB_SAAS_EXPECTED_USERS': str(receipt['existing_users']), 'WEB_SAAS_EXPECTED_SUBSCRIPTIONS': str(receipt['subscriptions']),
                    'WEB_SAAS_EXPECTED_SCHEMA_SHA256': receipt['schema_sha256'], 'WEB_SAAS_EXPECTED_DATA_SHA256': receipt['data_sha256'],
                    'WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER': receipt['database_system_identifier']}
                restored = script('restore_verify.sh', restore_env)
                self.assertEqual(restored.returncode, 0, restored.stderr)
                proof = json.loads(restored.stdout)
                self.assertTrue(proof['restored_data_matches'])
                self.assertTrue(proof['full_database'])
                self.assertFalse(proof['business_acceptance'])
                self.assertEqual(self.q('postgres', "SELECT count(*) FROM pg_database WHERE datname LIKE 'release_verify_%'"), '0')
                archive.write_bytes(archive.read_bytes() + b'tamper')
                self.assertNotEqual(script('restore_verify.sh', restore_env).returncode, 0)
                if run_id == '98765432101':
                    self.q('account', 'DELETE FROM subscriptions; DELETE FROM users;')
                else:
                    self.assertEqual(receipt['existing_users'], 0)
                    self.assertEqual(proof['g3_status'], 'blocked')
            self.assertEqual(self.q('account', "SELECT extra_domain.marker()"), 'fixture')


if __name__ == '__main__':
    unittest.main()
