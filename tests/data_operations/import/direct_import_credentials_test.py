import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/data_operations/data-migration_accounts_assert-credentials.sh'

class CredentialsTest(unittest.TestCase):
    def check_guard(self, source, target, project, expected):
        result = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True,
            env=dict(os.environ, MIGRATION_SOURCE_DSN=source, MIGRATION_TARGET_DSN=target,
                     MIGRATION_SOURCE_PROJECT_REF=project))
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn('synthetic-password', result.stdout + result.stderr)

    def test_legacy(self):
        self.check_guard('postgres://readonly:synthetic-password@db.svc.plus/account',
                         'postgres://account_user:synthetic-password@db.onwalk.net/account', '', 0)

    def test_bound_supabase(self):
        self.check_guard('postgres://readonly.project:synthetic-password@aws.pooler.supabase.com/postgres',
                         'postgres://account_user:synthetic-password@db.onwalk.net/account', 'project', 0)

    def test_admin_and_private_target(self):
        self.check_guard('postgres://postgres.project:synthetic-password@aws.pooler.supabase.com/postgres',
                         'postgres://account_user:synthetic-password@172.18.0.2/account', 'project', 1)

    def test_wrong_project(self):
        self.check_guard('postgres://readonly.other:synthetic-password@aws.pooler.supabase.com/postgres',
                         'postgres://account_user:synthetic-password@db.onwalk.net/account', 'project', 1)

    def test_connection_override(self):
        self.check_guard('postgres://readonly:synthetic-password@db.svc.plus/account?host=evil.test',
                         'postgres://account_user:synthetic-password@db.onwalk.net/account', '', 1)

if __name__ == '__main__':
    unittest.main()
