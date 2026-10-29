import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

PATH = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_v6_tls_identity/files/signing_keys.py'
spec = importlib.util.spec_from_file_location('signing_keys', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class SigningKeyTests(unittest.TestCase):
    def setUp(self):
        self.discovery = json.dumps({'issuer': 'https://kubernetes.default.svc.cluster.local'})
        self.key = {'kty': 'RSA', 'kid': 'key1', 'alg': 'RS256', 'use': 'sig',
                    'n': b64(b'\xc5' + b'\x31' * 255), 'e': b64(b'\x01\x00\x01')}

    def render(self, keys):
        return module.discovery_contract(self.discovery, json.dumps({'keys': keys}))

    def test_encodes_public_key_accepted_by_openssl(self):
        issuer, keys = self.render([self.key])
        self.assertEqual(issuer, json.loads(self.discovery)['issuer'])
        result = subprocess.run(['openssl', 'pkey', '-pubin', '-noout'], input=keys[0], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_weak_or_private_key(self):
        for change in ({'n': b64(b'\x01')}, {'d': 'secret'}, {'alg': 'HS256'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.render([{**self.key, **change}])

    def test_rejects_ambiguous_key_ids(self):
        with self.assertRaises(ValueError):
            self.render([self.key, self.key])

    def test_retains_rotation_key_set(self):
        _, keys = self.render([self.key, {**self.key, 'kid': 'key2'}])
        self.assertEqual(len(keys), 2)


if __name__ == '__main__':
    unittest.main()
