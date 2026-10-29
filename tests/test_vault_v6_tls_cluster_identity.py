import base64
import importlib.util
import json
from pathlib import Path
import sys
import unittest

DIR = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_v6_tls_identity/files'
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location('cluster_identity', DIR / 'cluster_identity.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
CLUSTER = '12345678-1234-1234-1234-123456789abc'
NODE = 'abcdef01-1234-1234-1234-123456789abc'


def b64(b): return base64.urlsafe_b64encode(b).decode().rstrip('=')


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.cluster = CLUSTER
        self.node = NODE
        self.ready = True

    def run_kubectl(self, *args):
        self.calls.append(args)
        if args[:3] == ('get', 'namespace', 'kube-system'):
            return json.dumps({'metadata': {'uid': self.cluster}})
        if args[:2] == ('get', 'nodes'):
            return json.dumps({'items': [{'metadata': {'name': m.NODE, 'uid': self.node},
                'status': {'nodeInfo': {'kubeletVersion': 'v1.37.1+k3s1'},
                           'conditions': [{'type': 'Ready', 'status': 'True' if self.ready else 'False'}]}}]})
        if args[:2] == ('get', 'serviceaccount'):
            return json.dumps({'metadata': {'name': args[2], 'namespace': 'vault-prod'}})
        if args[-1] == '/.well-known/openid-configuration': return json.dumps({'issuer': 'https://issuer.example'})
        if args[-1] == '/openid/v1/jwks':
            return json.dumps({'keys': [{'kty': 'RSA', 'kid': '1', 'n': b64(b'\xc5'+b'\x31'*255), 'e': b64(b'\x01\x00\x01')}]})
        if args[:2] == ('create', 'token'): return 'header.payload.signature'
        raise AssertionError(args)

    def test_collects_only_after_receipt_bound_target_check(self):
        result = m.collect(CLUSTER, NODE, 'v1.37.1+k3s1', self.run_kubectl)
        self.assertEqual(result['issuer'], 'https://issuer.example')
        self.assertEqual(len(result['public_keys']), 1)
        tokens = [args for args in self.calls if args[:2] == ('create', 'token')]
        self.assertEqual(len(tokens), 3)
        self.assertEqual([t[2] for t in tokens], ['vault-tls-reader', 'vault-tls-reader', 'default'])
        self.assertTrue(all(t[-2:] == ('--duration','600s') for t in tokens))

    def test_mismatch_or_unready_never_requests_token(self):
        for attr, value in [('cluster', NODE), ('node', CLUSTER), ('ready', False)]:
            self.setUp(); setattr(self, attr, value)
            with self.assertRaises(m.ClusterError): m.collect(CLUSTER, NODE, 'v1.37.1+k3s1', self.run_kubectl)
            self.assertFalse(any(args[:2] == ('create', 'token') for args in self.calls))

    def test_bad_receipt_uid_fails_before_cluster_call(self):
        with self.assertRaises(m.ClusterError): m.collect('bad', NODE, 'v1.37.1+k3s1', self.run_kubectl)
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
