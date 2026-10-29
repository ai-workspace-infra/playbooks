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
        self.calls=[];self.mounts={};self.policies={};self.configs={};self.roles={};self.scoped_policies=[SPEC.policy];self.denied_status=403;self.bad_login_status=400
        self.mount_list_status=200;self.mount_create_status=204;self.capabilities=['create','update','sudo']
    def request(self,method,path,body=None,read_json=True):
        self.calls.append((method,path,body,read_json))
        if path=='sys/auth' and method=='GET':return self.mount_list_status,{'data':self.mounts}
        if path=='sys/capabilities-self' and method=='POST':return 200,{'capabilities':self.capabilities}
        if path.startswith('sys/policies/acl/'):
            if method=='GET':return (404,None) if path not in self.policies else (200,{'data':{'policy':self.policies[path]}})
            if method=='PUT':self.policies[path]=body['policy'];return 204,None
        if path.startswith('sys/auth/') and method=='POST':
            if self.mount_create_status not in {200,204}:return self.mount_create_status,None
            mount=path.removeprefix('sys/auth/')+'/'
            self.mounts[mount]={'type':'jwt'};self.configs[mount]={'bound_issuer':'','jwt_validation_pubkeys':[]};return 204,None
        if path.startswith('auth/') and path.endswith('/config'):
            mount=path.removeprefix('auth/').removesuffix('/config')+'/'
            if method=='GET':return 200,{'data':self.configs.get(mount,{'bound_issuer':'','jwt_validation_pubkeys':[]})}
            if method=='POST':self.configs[mount]=body;return 204,None
        if path.startswith('auth/') and '/role/' in path:
            if method=='GET':return (404,None) if path not in self.roles else (200,{'data':self.roles[path]})
            if method=='POST':self.roles[path]=body;return 204,None
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
        self.assertEqual([x[1] for x in writes],['sys/capabilities-self','sys/policies/acl/'+SPEC.policy,'sys/auth/'+SPEC.mount,'auth/'+SPEC.mount+'/config','auth/'+SPEC.mount+'/role/'+SPEC.role])
        self.assertEqual(writes[-1][2],SPEC.role_config);self.assertEqual(writes[1][2]['policy'],SPEC.policy_hcl)

    def test_existing_state_causes_no_write(self):
        for kind in ('mount','policy'):
            c=Fake()
            if kind=='mount':c.mounts[SPEC.mount+'/']={'type':'kv'}
            else:c.policies['sys/policies/acl/'+SPEC.policy]='path "wrong" {}'
            with self.assertRaises(v.VaultError):v.provision(c,SPEC,ISSUER,['key'])
            self.assertFalse(any(path.startswith(('sys/auth/','sys/policies/acl/','auth/')) and method in {'PUT','POST'}
                                 for method,path,_,_ in c.calls))

    def test_mount_diagnostics_keep_only_operation_and_http_status(self):
        c=Fake();c.mount_list_status=403
        with self.assertRaises(v.VaultError) as caught:v.provision(c,SPEC,ISSUER,['key'])
        self.assertEqual(caught.exception.stage,'mount')
        self.assertEqual(caught.exception.diagnostic,{'operation':'auth_mount_list','http_status':403})
        c=Fake();c.mount_create_status=403
        with self.assertRaises(v.VaultError) as caught:v.provision(c,SPEC,ISSUER,['key'])
        self.assertEqual(caught.exception.diagnostic,{'operation':'auth_mount_create','http_status':403})

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

    def test_exact_policy_partial_state_continues_before_mount_creation(self):
        c=Fake();c.policies['sys/policies/acl/'+SPEC.policy]=SPEC.policy_hcl
        v.provision(c,SPEC,ISSUER,['public-key'])
        self.assertEqual([x[1] for x in c.calls if x[0]!='GET'],[
            'sys/capabilities-self','sys/auth/'+SPEC.mount,'auth/'+SPEC.mount+'/config','auth/'+SPEC.mount+'/role/'+SPEC.role])

if __name__=='__main__':unittest.main()
