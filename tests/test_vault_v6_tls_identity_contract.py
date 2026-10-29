import base64
import json
from pathlib import Path
import sys
import unittest

DIR=Path(__file__).resolve().parents[1]/'roles/vhosts/vault_jwt_workload_identity/files'
sys.path.insert(0,str(DIR))
import vault_contract as v
ISSUER='https://issuer.example'
SPEC=v.IdentitySpec('jwt-test','reader','reader-policy','test-ns','reader-sa','aud-test','aud-denied','probe-sa','kv/data/test/cert','kv/data/test/denied')


def jwt(subject=SPEC.subject,audience=SPEC.audience):
    payload=base64.urlsafe_b64encode(json.dumps({'iss':ISSUER,'sub':subject,'aud':[audience]}).encode()).decode().rstrip('=')
    return 'header.'+payload+'.signature'


class Fake:
    def __init__(self):
        self.calls=[];self.mounts={};self.policy_status=404;self.scoped_policies=[SPEC.policy];self.denied_status=403;self.bad_login_status=400
    def request(self,method,path,body=None,read_json=True):
        self.calls.append((method,path,body,read_json))
        if path=='sys/auth' and method=='GET':return 200,{'data':self.mounts}
        if path.startswith('sys/policies/acl/') and method=='GET':return self.policy_status,None
        if path.endswith('/login'):
            if body['jwt']==jwt():return 200,{'auth':{'client_token':'test-only','policies':self.scoped_policies}}
            return self.bad_login_status,None
        if path==SPEC.allowed_path:return 200,None
        if path==SPEC.denied_path:return self.denied_status,None
        return 204,None


class Contract(unittest.TestCase):
    def test_provision_dedicated_mount_with_exact_boundary(self):
        c=Fake();v.provision(c,SPEC,ISSUER,['public-key'])
        writes=[(method,path,body) for method,path,body,_ in c.calls if method!='GET']
        self.assertEqual([x[1] for x in writes],['sys/policies/acl/'+SPEC.policy,'sys/auth/'+SPEC.mount,'auth/'+SPEC.mount+'/config','auth/'+SPEC.mount+'/role/'+SPEC.role])
        self.assertEqual(writes[-1][2],SPEC.role_config);self.assertEqual(writes[0][2]['policy'],SPEC.policy_hcl)

    def test_existing_state_causes_no_write(self):
        for attr,value in [('mounts',{SPEC.mount+'/':{}}),('policy_status',200),('policy_status',403)]:
            c=Fake();setattr(c,attr,value)
            with self.assertRaises(v.VaultError):v.provision(c,SPEC,ISSUER,['key'])
            self.assertFalse(any(method!='GET' for method,_,_,_ in c.calls))

    def test_allowed_and_denied_without_reading_secret(self):
        c=Fake();result=v.verify(lambda token:c,SPEC,ISSUER,jwt(),jwt(audience=SPEC.denied_audience),jwt(subject=SPEC.probe_subject))
        self.assertEqual(result['result'],'VAULT_WORKLOAD_IDENTITY_ALLOW_DENY_VERIFIED')
        for method,_,_,read in c.calls:
            if method=='GET':self.assertFalse(read)

    def test_fail_closed_for_broad_policy_or_unexpected_access(self):
        for attr,value in [('scoped_policies',[SPEC.policy,'default']),('denied_status',200),('bad_login_status',200)]:
            c=Fake();setattr(c,attr,value)
            with self.assertRaises(v.VaultError):v.verify(lambda token:c,SPEC,ISSUER,jwt(),jwt(audience=SPEC.denied_audience),jwt(subject=SPEC.probe_subject))

    def test_rejects_wrong_probe_claims_before_login(self):
        c=Fake()
        with self.assertRaises(v.VaultError):v.verify(lambda token:c,SPEC,ISSUER,jwt(),jwt(),jwt())
        self.assertFalse(c.calls)

    def test_rejects_wildcard_and_unscoped_configuration(self):
        from dataclasses import replace
        for change in ({'allowed_path':'kv/data/*'},{'denied_path':SPEC.allowed_path},{'probe_service_account':SPEC.service_account},{'ttl_seconds':601}):
            with self.subTest(change=change),self.assertRaises(v.VaultError):replace(SPEC,**change)

    def test_second_workload_is_parameterized(self):
        from dataclasses import replace
        other=replace(SPEC,mount='jwt-test-two',role='reader-two',policy='policy-two',namespace='another-ns',service_account='another-sa',allowed_path='kv/data/another/cert',denied_path='kv/data/another/denied')
        c=Fake();v.provision(c,other,ISSUER,['key'])
        self.assertEqual(c.calls[-1][1],'auth/jwt-test-two/role/reader-two')
        self.assertEqual(c.calls[-1][2]['bound_subject'],'system:serviceaccount:another-ns:another-sa')

if __name__=='__main__':unittest.main()
