import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

DIR = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_v6_tls_identity/files'
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
                m.execute('verify', raw, digest, sha, lambda *a: calls.append(a), lambda *a: calls.append(a))
            self.assertEqual(calls, [])

    def test_verify_routes_ephemeral_tokens_in_memory_and_redacts_receipt(self):
        raw=receipt(); marker='jwt-not-for-output'
        material={'issuer':'issuer','public_keys':['public-key'],'good_jwt':marker,
                  'wrong_audience_jwt':'bad-aud','wrong_service_account_jwt':'bad-sa'}
        with patch.object(m.vault_contract,'verify',return_value={'result':'VAULT_TLS_IDENTITY_ALLOW_DENY_VERIFIED'}) as verify:
            result=m.execute('verify',raw,checksum(raw),SHA,lambda *a:None,lambda *a:material)
        self.assertEqual(result['cluster_uid'],CLUSTER)
        self.assertNotIn(marker,json.dumps(result))
        self.assertEqual(verify.call_args.args[1:],('issuer',marker,'bad-aud','bad-sa'))

    def test_configure_uses_admin_only_after_cluster_collection(self):
        raw=receipt(); calls=[]
        def collect(*args): calls.append('cluster'); return {'issuer':'issuer','public_keys':['public-key']}
        def factory(admin,*args): calls.append(('admin',admin)); return 'client'
        with patch.object(m.vault_contract,'provision') as provision:
            result=m.execute('configure',raw,checksum(raw),SHA,factory,collect)
        self.assertEqual(calls,['cluster',('admin',True)])
        provision.assert_called_once_with('client','issuer',['public-key'])
        self.assertEqual(result['result'],'VAULT_TLS_IDENTITY_CONFIGURED')

if __name__ == '__main__': unittest.main()
