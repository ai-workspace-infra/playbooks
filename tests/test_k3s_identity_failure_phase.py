import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('identity_phase', ROOT / 'roles/vhosts/k3s_identity/files/bootstrap_k3s_identity_keys.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FailurePhaseTest(unittest.TestCase):
    def test_archive_error_reports_only_phase_code(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = ['identity', '--environment', 'prod', '--cluster', 'example', '--cluster-uid', 'uid', '--api-server', 'https://localhost', '--admin-kubeconfig', '/tmp/config', '--client-ca', '/tmp/ca', '--client-ca-key', '/tmp/key', '--server-ca', '/tmp/server', '--state-dir', directory]
            error = io.StringIO()
            with patch.object(sys, 'argv', argv), patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'run', return_value='uid'), patch.object(m, 'directory'), patch.object(m, 'prepare', return_value={}), patch.object(m, 'Vault') as vault, redirect_stderr(error):
                vault.return_value.archive.side_effect = ValueError('sensitive marker')
                self.assertEqual(m.main(), 23)
            self.assertNotIn('sensitive marker', error.getvalue())

if __name__ == '__main__': unittest.main()
