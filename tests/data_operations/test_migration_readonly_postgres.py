"""Real privilege checks; accepts only an explicitly named isolated Docker fixture."""
import os
import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MigrationReaderPostgresTest(unittest.TestCase):
    def test_local_reader_is_idempotent_and_never_changes_the_business_identity(self):
        container = os.environ.get('MIGRATION_READER_TEST_CONTAINER', '')
        if not container:
            self.skipTest('isolated PostgreSQL 17 Docker fixture not configured')
        self.assertTrue(container.startswith('migration-reader-fixture-'))

        def sql(query, user='postgres', check=True):
            result = subprocess.run(
                ['docker', 'exec', '-i', '--user', 'postgres', container, 'psql',
                 '-XAtq', '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', 'account'],
                input=query, capture_output=True, text=True)
            if check:
                self.assertEqual(result.returncode, 0, result.stderr)
            return result

        version = int(sql('SHOW server_version_num;').stdout.strip())
        self.assertGreaterEqual(version, 170000)
        self.assertLess(version, 180000)
        sql("CREATE ROLE app_fixture LOGIN PASSWORD 'synthetic-fixture'; "
            'CREATE TABLE public.audit_fixture(id bigint PRIMARY KEY); '
            'CREATE SEQUENCE public.audit_fixture_seq; '
            'INSERT INTO public.audit_fixture VALUES(1); '
            'ALTER TABLE public.audit_fixture ENABLE ROW LEVEL SECURITY;')
        business_before = sql("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='app_fixture';").stdout
        template = (ROOT / 'roles/readonly_ssh_user/templates/postgresql-passwordless.sql.j2').read_text()
        rendered = template.replace('{{ readonly_database_name }}', 'account')
        self.assertNotIn('{{', rendered)
        self.assertIn('changed', sql(rendered).stdout.splitlines())
        self.assertIn('unchanged', sql(rendered).stdout.splitlines())
        self.assertEqual(sql('SELECT count(*) FROM public.audit_fixture;', 'readonly_release').stdout.strip(), '1')
        self.assertEqual(sql('SHOW default_transaction_read_only;', 'readonly_release').stdout.strip(), 'on')
        # Disabling the session default must not bypass real table permissions.
        rejected = sql('SET default_transaction_read_only=off; INSERT INTO public.audit_fixture VALUES(2);',
                       'readonly_release', check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('permission denied', rejected.stderr)
        rejected = sql("SET default_transaction_read_only=off; SELECT nextval('public.audit_fixture_seq');",
                       'readonly_release', check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('permission denied', rejected.stderr)
        self.assertEqual(sql('SELECT count(*) FROM public.audit_fixture;').stdout.strip(), '1')
        self.assertEqual(sql("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='app_fixture';").stdout,
                         business_before)
        # Existing incompatible credentials are preserved, not silently rotated.
        sql("ALTER ROLE readonly_release PASSWORD 'existing-fixture-credential';")
        role_before = sql("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='readonly_release';").stdout
        rejected = sql(rendered, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('manual review required', rejected.stderr)
        self.assertEqual(sql("SELECT md5(rolpassword) FROM pg_authid WHERE rolname='readonly_release';").stdout,
                         role_before)

        restore_container = os.environ.get('MIGRATION_READER_RESTORE_TEST_CONTAINER', '')
        if restore_container:
            self.assertTrue(restore_container.startswith('migration-reader-restore-fixture-'))
            spec = importlib.util.spec_from_file_location(
                'snapshot_policy_roles', ROOT / 'roles/web_saas_full_snapshot/files/policy_roles.py')
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)

            def query(database, statement, selected=container):
                result = subprocess.run(['docker', 'exec', '-i', '--user', 'postgres', selected,
                                         'psql', '-XAtq', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres',
                                         '-d', database], input=statement, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            self.assertEqual(helper.restore_policy_role_references(query, restore_container), 1)
            dump = subprocess.run(['docker', 'exec', '--user', 'postgres', container, 'pg_dump',
                                   '-U', 'readonly_release', '-d', 'account', '-Fc', '--enable-row-security'],
                                  capture_output=True)
            self.assertEqual(dump.returncode, 0, dump.stderr)
            restored = subprocess.run(['docker', 'exec', '-i', '--user', 'postgres', restore_container,
                                       'pg_restore', '-U', 'postgres', '-d', 'account', '--no-owner',
                                       '--no-acl', '--single-transaction', '--exit-on-error'],
                                      input=dump.stdout, capture_output=True)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(query('account', 'SELECT count(*) FROM public.audit_fixture;', restore_container), '1')
            self.assertEqual(query('postgres', "SELECT rolcanlogin::text||':'||(rolpassword IS NULL)::text "
                                              "FROM pg_authid WHERE rolname='readonly_release';", restore_container),
                             'false:true')


if __name__ == '__main__':
    unittest.main()
