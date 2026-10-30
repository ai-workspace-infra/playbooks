import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('k3s_identity', ROOT / 'roles/vhosts/k3s_platform_bootstrap/files/bootstrap_k3s_identity_keys.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Credentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        os.chmod(self.base, 0o700)
        m.run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.base / 'ca.key'),
              '-out', str(self.base / 'ca.crt'), '-days', '5', '-subj', '/CN=test-client-ca')
        self.args = argparse.Namespace(state_dir=self.base, environment='sit', cluster='sample', cluster_uid='uid-1',
            api_server='https://127.0.0.1:6443', client_ca=self.base / 'ca.crt', client_ca_key=self.base / 'ca.key',
            server_ca=self.base / 'ca.crt', days=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_three_unique_credentials_and_reuse(self):
        records = {r: m.prepare(self.args, r) for r in m.ROLES}
        self.assertEqual(len({r['client_private_key'] for r in records.values()}), 3)
        for role, record in records.items():
            self.assertEqual(m.prepare(self.args, role), record)
            self.assertEqual((self.base / role / 'record.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(record['kubeconfig'])['users'][0]['name'], role)
            self.assertNotIn('ca.key', record)

    def test_wrong_target_refused(self):
        m.prepare(self.args, 'operator')
        self.args.cluster_uid = 'different'
        with self.assertRaises(ValueError):
            m.prepare(self.args, 'operator')

    def test_partial_issuance_refused(self):
        m.directory(self.base / 'auditor')
        m.private_write(self.base / 'auditor' / 'client.key', 'partial')
        with self.assertRaises(ValueError):
            m.prepare(self.args, 'auditor')

    def test_wrong_ca_refused(self):
        m.prepare(self.args, 'auditor')
        m.run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(self.base / 'wrong.key'),
              '-out', str(self.base / 'wrong.crt'), '-days', '5', '-subj', '/CN=wrong-ca')
        with self.assertRaises(ValueError):
            m.verify('auditor', self.base / 'auditor', self.base / 'wrong.crt')

    def test_changed_endpoint_refused(self):
        m.prepare(self.args, 'root')
        self.args.api_server = 'https://another.invalid'
        with self.assertRaises(ValueError):
            m.prepare(self.args, 'root')


class Archival(unittest.TestCase):
    def test_create_uses_cas_zero_and_reads_back(self):
        vault = m.Vault.__new__(m.Vault)
        vault.request = Mock(side_effect=[None, {}, {'data': {'data': {'subject': 'auditor'}}}])
        self.assertTrue(vault.archive('sit/sample/auditor', {'subject': 'auditor'}))
        self.assertEqual(vault.request.call_args_list[1].args[1]['options']['cas'], 0)

    def test_existing_identical_record_is_noop(self):
        vault = m.Vault.__new__(m.Vault)
        vault.request = Mock(return_value={'data': {'data': {'subject': 'auditor'}}})
        self.assertFalse(vault.archive('sit/sample/auditor', {'subject': 'auditor'}))
        self.assertEqual(vault.request.call_count, 1)

    def test_existing_other_record_is_not_overwritten(self):
        vault = m.Vault.__new__(m.Vault)
        vault.request = Mock(return_value={'data': {'data': {'subject': 'root'}}})
        with self.assertRaises(ValueError):
            vault.archive('sit/sample/auditor', {'subject': 'auditor'})
        self.assertEqual(vault.request.call_count, 1)

    def test_http_vault_refused(self):
        with self.assertRaises(ValueError):
            m.Vault('http://localhost:8200', 'test')


if __name__ == '__main__':
    unittest.main()
