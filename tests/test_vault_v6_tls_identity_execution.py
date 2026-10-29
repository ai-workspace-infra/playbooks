import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'scripts/v6_vault_tls_identity'
sys.path.insert(0, str(ROOT / 'roles/vhosts/vault_jwt_workload_identity/files'))
sys.path.insert(0, str(DIR))
spec = importlib.util.spec_from_file_location('identity_execution', DIR / 'identity_execution.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA = 'a' * 40
CLUSTER = '12345678-1234-1234-1234-123456789abc'
NODE = 'abcdef01-1234-1234-1234-123456789abc'


def receipt(**change):
    data = {'result': 'PAUSED_BOOTSTRAP_VERIFIED', 'node': m.NODE, 'version': m.VERSION,
            'gitops_sha': SHA, 'audit': {'result': 'AUDIT_ALLOW_DENY_VERIFIED'},
            'common': {'result': 'COMMON_NODE_VERIFIED', 'node': m.NODE},
            'cluster_uid': CLUSTER, 'node_uid': NODE}
    data.update(change)
    return json.dumps(data).encode()


def checksum(raw): return hashlib.sha256(raw).hexdigest()


class ExecutionTests(unittest.TestCase):
    def test_invalid_receipts_fail_before_cluster_or_vault(self):
        for raw, digest, sha in [(receipt(), '0'*64, SHA), (receipt(node='other'), checksum(receipt(node='other')), SHA),
                                 (receipt(audit={'result':'FAIL'}), checksum(receipt(audit={'result':'FAIL'})), SHA),
                                 (receipt(), checksum(receipt()), 'b'*40)]:
            calls=[]
            with self.assertRaises(m.IdentityError):
                m.receipt_contract(raw, digest, sha)
            self.assertEqual(calls, [])


if __name__ == '__main__': unittest.main()
