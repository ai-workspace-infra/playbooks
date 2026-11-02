import os
from pathlib import Path
import subprocess
import unittest
import urllib.parse
import uuid


class ReceiptBootstrapTest(unittest.TestCase):
    def test_isolated_postgres(self):
        dsn = os.environ.get('ACCOUNTS_IDENTITY_TEST_DSN')
        if not dsn:
            self.skipTest('dedicated loopback PostgreSQL required')
        cfg = urllib.parse.urlparse(dsn)
        if cfg.hostname not in ('localhost', '127.0.0.1') or cfg.username != 'postgres' or cfg.path != '/postgres' or cfg.password:
            self.fail('requires dedicated loopback postgres fixture')
        base = ['psql', '-X', '-q', '-h', cfg.hostname, '-p', str(cfg.port or 5432), '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1']
        name = 'receipt_fixture_' + uuid.uuid4().hex
        def run(db, sql):
            return subprocess.run(base + ['-d', db], input=sql, text=True, capture_output=True)
        self.assertEqual(run('postgres', "SELECT count(*) FROM pg_roles WHERE rolname='accounts_migration';").stdout.strip(), '0')
        self.assertEqual(run('postgres', 'CREATE DATABASE "' + name + '"; CREATE ROLE accounts_migration LOGIN NOINHERIT;').returncode, 0)
        try:
            script = (Path(__file__).resolve().parents[1] / 'roles/accounts_migration_identity/files/business_receipt_bootstrap.sql').read_text()
            refused = run(name, script)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(run(name, "SELECT to_regnamespace('accounts_migration_api') IS NULL;").stdout.strip(), 't')
            settings = "SELECT set_config('accounts_migration_guard.system_identifier',(SELECT system_identifier::text FROM pg_control_system()),false); SELECT set_config('accounts_migration_guard.database_oid',(SELECT oid::text FROM pg_database WHERE datname=current_database()),false);\n"
            self.assertEqual(run('postgres', 'CREATE ROLE receipt_intruder NOLOGIN;').returncode, 0)
            self.assertEqual(run(name, 'ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO receipt_intruder;').returncode, 0)
            broad = run(name, settings + script)
            self.assertNotEqual(broad.returncode, 0)
            self.assertEqual(run(name, "SELECT to_regnamespace('accounts_migration_api') IS NULL;").stdout.strip(), 't')
            self.assertEqual(run(name, 'ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM receipt_intruder;').returncode, 0)
            ok = run(name, settings + script)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            role = run(name, "SET ROLE accounts_migration; INSERT INTO accounts_migration_api.receipts(plan_id,plan_sha256,subject,receipt) VALUES('00000000-0000-4000-8000-000000000001','fixture','fixture','{}'); SELECT count(*) FROM accounts_migration_api.receipts;")
            self.assertEqual(role.returncode, 0, role.stderr)
            self.assertEqual(role.stdout.strip(), '1')
            for statement in ('UPDATE accounts_migration_api.receipts SET subject=\'changed\';', 'DELETE FROM accounts_migration_api.receipts;', 'CREATE TABLE accounts_migration_api.extra(id int);'):
                denied = run(name, 'SET ROLE accounts_migration; ' + statement)
                self.assertNotEqual(denied.returncode, 0)
            retry = run(name, settings + script)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(run(name, 'SELECT count(*) FROM accounts_migration_api.receipts;').stdout.strip(), '1')
        finally:
            self.assertEqual(run('postgres', 'DROP DATABASE "' + name + '" WITH (FORCE);').returncode, 0)
            self.assertEqual(run('postgres', 'DROP ROLE accounts_migration;').returncode, 0)
            self.assertEqual(run('postgres', 'DROP ROLE IF EXISTS receipt_intruder;').returncode, 0)


if __name__ == '__main__':
    unittest.main()
