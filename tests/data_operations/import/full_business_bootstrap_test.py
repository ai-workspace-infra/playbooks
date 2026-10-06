import importlib.util
import json
import pathlib
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/data_operations'))
import bootstrap_full_business_credentials as subject


class BootstrapContractTests(unittest.TestCase):
    def test_failed_read_is_not_assumed_absent(self):
        with patch.object(subject.subprocess, 'run', return_value=SimpleNamespace(returncode=2)), patch.object(subject, 'run', return_value='["database-upgrade"]'):
            with self.assertRaises(RuntimeError):
                subject.read_upgrade()

    def test_absence_requires_verified_listing(self):
        with patch.object(subject.subprocess, 'run', return_value=SimpleNamespace(returncode=2)), patch.object(subject, 'run', return_value='["databases"]'):
            self.assertEqual(subject.read_upgrade(), ({}, 0))

    def test_compare_and_swap_preserves_unknown_fields(self):
        payload = {'unrelated': 'preserved', 'BOOTSTRAP_STATE': 'pending'}
        with patch.object(subject, 'run') as run, patch.object(subject, 'read_upgrade', return_value=(payload, 4)):
            self.assertEqual(subject.save(payload, 3), 4)
            args, kwargs = run.call_args
            self.assertIn('-cas=3', args[0])
            self.assertEqual(json.loads(kwargs['input']), payload)

    def test_concurrent_vault_change_is_rejected(self):
        with patch.object(subject, 'run'), patch.object(subject, 'read_upgrade', return_value=({'unexpected': True}, 5)):
            with self.assertRaises(RuntimeError):
                subject.save({'BOOTSTRAP_STATE': 'pending'}, 3)

    def test_saved_role_cannot_cross_project(self):
        p = subject.urlsplit('postgresql://postgres.project-a:private@region.pooler.supabase.com:5432/postgres')
        dsn = subject.readonly_dsn(p, 'project-a', 'x' * 48)
        subject.validate_saved_login(dsn, p, 'project-a')
        with self.assertRaises(RuntimeError):
            subject.validate_saved_login(dsn, p, 'project-b')

    def test_connection_requires_tls(self):
        env = subject.connection_env('postgresql://readonly_release.project:private@region.pooler.supabase.com:5432/postgres?sslmode=disable')
        self.assertEqual(env['PGSSLMODE'], 'require')


if __name__ == '__main__':
    unittest.main()
