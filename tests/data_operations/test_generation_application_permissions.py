"""Run against a disposable PostgreSQL 17 container, never an application DB."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'roles/web_saas_generation_fresh/files/application_permissions.py'
spec = importlib.util.spec_from_file_location('application_permissions', MODULE)
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)
CONTAINER = os.environ.get('GENERATION_PERMISSIONS_PG_CONTAINER', '')


@unittest.skipUnless(CONTAINER.startswith('generation-permissions-fixture-'),
                     'requires an explicitly named disposable PostgreSQL 17 fixture')
class GenerationApplicationPermissionsTests(unittest.TestCase):
    def pg(self, query, role='postgres', ok=True):
        result = subprocess.run(
            ['docker', 'exec', '-i', CONTAINER, 'psql', '-U', role, '-d', 'account',
             '-XAtq', '-v', 'ON_ERROR_STOP=1'],
            input=query, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout.strip()

    @classmethod
    def setUpClass(cls):
        subprocess.run(['docker', 'exec', CONTAINER, 'createdb', '-U', 'postgres', 'account'], check=True)
        cls().pg("""
            CREATE ROLE generation_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
              NOINHERIT NOREPLICATION BYPASSRLS;
            ALTER ROLE generation_app SET default_transaction_read_only=on;
            CREATE ROLE readonly_release LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
              NOINHERIT NOREPLICATION NOBYPASSRLS;
            ALTER ROLE readonly_release SET default_transaction_read_only=on;
            CREATE TABLE schema_migrations(version bigint, dirty boolean);
            INSERT INTO schema_migrations VALUES(2026102002,false);
            CREATE TABLE users(id bigserial PRIMARY KEY, email text UNIQUE);
            ALTER TABLE users ENABLE ROW LEVEL SECURITY;
            ALTER TABLE users FORCE ROW LEVEL SECURITY;
            CREATE TABLE sessions(id bigserial PRIMARY KEY, user_id bigint REFERENCES users);
            CREATE TABLE billing_ledger(id bigserial PRIMARY KEY, user_id bigint REFERENCES users, bytes bigint);
            CREATE TABLE traffic_minute_buckets(id bigserial PRIMARY KEY, user_id bigint REFERENCES users, bytes bigint);
            CREATE TABLE migration_receipts(id bigserial PRIMARY KEY);
            GRANT USAGE ON SCHEMA public TO readonly_release;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_release;
            CREATE POLICY audit_read ON users FOR SELECT TO readonly_release USING(true);
        """)

    def test_primary_business_writes_and_audit_denial(self):
        self.pg(permissions.primary_permissions_sql())
        self.assertEqual(self.pg('SHOW default_transaction_read_only', 'generation_app'), 'off')
        self.assertEqual(self.pg("""
            BEGIN;
            INSERT INTO users(email) VALUES('fixture@example.invalid') RETURNING id;
            INSERT INTO sessions(user_id) SELECT id FROM users;
            INSERT INTO billing_ledger(user_id,bytes) SELECT id,42 FROM users;
            INSERT INTO traffic_minute_buckets(user_id,bytes) SELECT id,42 FROM users;
            UPDATE billing_ledger SET bytes=43;
            DELETE FROM sessions;
            SELECT count(*) FROM billing_ledger WHERE bytes=43;
            ROLLBACK;
        """, 'generation_app').splitlines()[-1], '1')
        self.assertEqual(self.pg('SELECT count(*) FROM users'), '0')
        for query in (
            'CREATE TABLE public.forbidden(id int)', 'TRUNCATE users',
            'INSERT INTO schema_migrations VALUES(0,false)',
            'INSERT INTO migration_receipts DEFAULT VALUES',
            "SELECT setval('users_id_seq',100)",
        ):
            self.pg(query, 'generation_app', ok=False)
        # Turning off the default read-only setting cannot bypass audit ACLs.
        self.pg('SET default_transaction_read_only=off; INSERT INTO users(email) VALUES(\'blocked\')',
                'readonly_release', ok=False)
        self.assertEqual(self.pg('SHOW default_transaction_read_only', 'readonly_release'), 'on')
        before = self.pg("SELECT md5(string_agg(relname||coalesce(relacl::text,''),'' ORDER BY relname)) FROM pg_class WHERE relnamespace='public'::regnamespace")
        self.pg(permissions.primary_permissions_sql())
        after = self.pg("SELECT md5(string_agg(relname||coalesce(relacl::text,''),'' ORDER BY relname)) FROM pg_class WHERE relnamespace='public'::regnamespace")
        self.assertEqual(before, after)

    def test_unexpected_privileges_roll_back_new_grants(self):
        self.pg('REVOKE INSERT, UPDATE, DELETE ON users FROM generation_app; ALTER ROLE generation_app CREATEDB')
        try:
            self.pg(permissions.primary_permissions_sql(), ok=False)
            self.assertEqual(self.pg("SELECT has_table_privilege('generation_app','users','INSERT')"), 'f')
        finally:
            self.pg('ALTER ROLE generation_app NOCREATEDB')


if __name__ == '__main__':
    unittest.main()
