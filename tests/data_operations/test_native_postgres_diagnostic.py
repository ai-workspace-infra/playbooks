"""Sanitized container startup diagnosis fixtures; never contacts a real host."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('native_pg_diagnostic', ROOT / 'scripts/data_operations/selfhost/native_postgres_diagnostic_host.py')
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class FakeDataPath:
    def is_dir(self): return True
    def is_symlink(self): return False
    def iterdir(self): return iter([])
    def __truediv__(self, key): return self
    def exists(self): return False


class DiagnosticTests(unittest.TestCase):
    def diagnostic(self, password='', logs=''):
        container = {'Config': {'Env': ['POSTGRES_PASSWORD=' + password, 'AUTH_TOKEN=fictional-do-not-print']},
            'State': {'Status': 'restarting', 'ExitCode': 1, 'OOMKilled': False, 'Error': 'fictional-do-not-print'}}
        output = io.StringIO()
        with patch.object(HOST, 'read', return_value=json.dumps([container])), \
                patch.object(HOST, 'Path', return_value=FakeDataPath()), \
                patch.object(HOST.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=logs, stderr='')), \
                contextlib.redirect_stdout(output):
            HOST.main()
        self.assertNotIn('fictional-do-not-print', output.getvalue())
        self.assertNotIn('POSTGRES_PASSWORD=', output.getvalue())
        return json.loads(output.getvalue())

    def test_missing_password_and_empty_cluster_classified_without_values(self):
        receipt = self.diagnostic(logs='Error: superuser password is not specified')
        self.assertFalse(receipt['postgres_password_present'])
        self.assertTrue(receipt['pgdata_empty'])
        self.assertFalse(receipt['pg_version_present'])
        self.assertIn('missing_postgres_password', receipt['error_codes'])

    def test_permission_and_existing_password_are_safe_booleans(self):
        receipt = self.diagnostic(password='fictional-do-not-print', logs='initdb: Permission denied')
        self.assertTrue(receipt['postgres_password_present'])
        self.assertIn('data_directory_permission_denied', receipt['error_codes'])


if __name__ == '__main__':
    unittest.main()
