import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DIR = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_jwt_workload_identity/files'
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location('remote_kubectl', DIR / 'remote_kubectl.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from vault_contract import IdentitySpec
IDENTITY=IdentitySpec('jwt-test','reader','reader','test-ns','tls-reader','aud-test','aud-denied','probe','kv/data/test/cert','kv/data/test/denied')


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.known = Path(self.tmp.name) / 'known_hosts'; self.known.write_text('known host key\n')
        self.remote = m.RemoteKubectl('192.0.2.1', self.known, IDENTITY, 'k3s', 'root')

    def test_allowed_command_uses_attested_host_and_no_agent_forwarding(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'safe-result\n', '')) as run:
            self.assertEqual(self.remote('get', 'nodes', '-o', 'json'), 'safe-result')
        argv=run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('ForwardAgent=no', argv)
        self.assertIn('UserKnownHostsFile='+str(self.known.resolve()), argv)
        self.assertEqual(argv[-1], 'k3s kubectl get nodes -o json')
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_unlisted_command_fails_before_ssh(self):
        with patch.object(m.subprocess, 'run') as run:
            with self.assertRaises(m.ClusterError): self.remote('delete','namespace','vault-prod')
            run.assert_not_called()

    def test_failure_does_not_disclose_stderr(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'sensitive marker')):
            with self.assertRaises(m.ClusterError) as exc: self.remote('get', 'nodes', '-o', 'json')
        self.assertNotIn('sensitive marker', str(exc.exception))

    def test_invalid_address_or_missing_attestation_rejected(self):
        for host in ['example.org', '0.0.0.0', '192.0.2.1;echo bad']:
            with self.subTest(host=host), self.assertRaises(m.ClusterError): m.RemoteKubectl(host, self.known, IDENTITY, 'k3s', 'root')
        with self.assertRaises(m.ClusterError): m.RemoteKubectl('192.0.2.1', self.known.parent / 'missing', IDENTITY, 'k3s', 'root')


if __name__ == '__main__': unittest.main()
