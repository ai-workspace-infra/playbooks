import base64
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_v6_tls_identity/files/vault_contract.py'
spec = importlib.util.spec_from_file_location('vault_contract', PATH)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
ISSUER = 'https://kubernetes.default.svc.cluster.local'


def jwt(subject=v.SUBJECT, audience=v.AUDIENCE):
    payload = base64.urlsafe_b64encode(json.dumps({'iss': ISSUER, 'sub': subject, 'aud': [audience]}).encode()).decode().rstrip('=')
    return 'header.' + payload + '.signature'


class Fake:
    def __init__(self):
        self.calls = []
        self.mounts = {}
        self.policy_status = 404
        self.scoped_policies = [v.NAME]
        self.denied_status = 403
        self.bad_login_status = 400

    def request(self, method, path, body=None, read_json=True):
        self.calls.append((method, path, body, read_json))
        if path == 'sys/auth' and method == 'GET': return 200, {'data': self.mounts}
        if path.startswith('sys/policies/acl/') and method == 'GET': return self.policy_status, None
        if path.endswith('/login'):
            if body['jwt'] == jwt(): return 200, {'auth': {'client_token': 'test-only', 'policies': self.scoped_policies}}
            return self.bad_login_status, None
        if path == v.ALLOWED: return 200, None
        if path == v.DENIED: return self.denied_status, None
        return 204, None


class Contract(unittest.TestCase):
    def test_provision_dedicated_mount_with_exact_boundary(self):
        c=Fake(); v.provision(c, ISSUER, ['public-key'])
        writes=[(method,path,body) for method,path,body,_ in c.calls if method!='GET']
        self.assertEqual([x[1] for x in writes], ['sys/policies/acl/'+v.NAME, 'sys/auth/'+v.MOUNT, 'auth/'+v.MOUNT+'/config', 'auth/'+v.MOUNT+'/role/'+v.NAME])
        self.assertEqual(writes[-1][2],v.ROLE)
        self.assertEqual(writes[0][2]['policy'],v.POLICY)

    def test_existing_state_causes_no_write(self):
        for attr, value in [('mounts',{v.MOUNT+'/': {}}),('policy_status',200),('policy_status',403)]:
            c=Fake(); setattr(c,attr,value)
            with self.assertRaises(v.VaultError): v.provision(c,ISSUER,['key'])
            self.assertFalse(any(method!='GET' for method,_,_,_ in c.calls))

    def test_validates_actual_allow_and_denials_without_reading_secret(self):
        c=Fake()
        result=v.verify(lambda token:c,ISSUER,jwt(),jwt(audience='vault-prod-k3s-v6-denied-probe'),jwt(subject='system:serviceaccount:vault-prod:default'))
        self.assertEqual(result['result'],'VAULT_TLS_IDENTITY_ALLOW_DENY_VERIFIED')
        for method,path,_,read in c.calls:
            if method=='GET': self.assertFalse(read)

    def test_fail_closed_for_broad_policy_or_unexpected_access(self):
        for attr,value in [('scoped_policies',[v.NAME,'default']),('denied_status',200),('bad_login_status',200)]:
            c=Fake(); setattr(c,attr,value)
            with self.assertRaises(v.VaultError):
                v.verify(lambda token:c,ISSUER,jwt(),jwt(audience='vault-prod-k3s-v6-denied-probe'),jwt(subject='system:serviceaccount:vault-prod:default'))

    def test_rejects_wrong_probe_claims_before_login(self):
        c=Fake()
        with self.assertRaises(v.VaultError): v.verify(lambda token:c,ISSUER,jwt(),jwt(),jwt())
        self.assertFalse(c.calls)

if __name__ == '__main__': unittest.main()
