import copy
import json
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('referral_acceptance', ROOT/'roles/referral_deployment_acceptance/files/accept.py')
m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NOW = dt.datetime(2026, 10, 27, tzinfo=dt.timezone.utc)


def fixture(stage='sit'):
    c = {'schema':'referral-deployment/v1','environment':'dev','stage':stage,'feature_id':'xconnect-referral',
         'sit_namespace':'feature-xconnect-referral-sit','uat_namespace':'web-saas-uat','namespace':'feature-xconnect-referral-sit' if stage=='sit' else 'web-saas-uat',
         'cluster':'dev-k3s-v5','instance_id':'cloud-instance-verified','candidate_fingerprint':'a'*64,'gitops_sha':'b'*40,
         'owner_sha':'c'*40,'run_id':'offline-fixture','data_baseline':'isolated-seed-v1','source_shas':{'accounts':'d'*40,'portal':'e'*40},
         'namespace_uid':'11111111-1111-1111-1111-111111111111','node_uid':'22222222-2222-2222-2222-222222222222','cluster_uid':'33333333-3333-3333-3333-333333333333',
         'origins':dict(zip(('accounts','console'),('https://'+h for h in m.LANES[stage][1:]))),
         'images':{k:'registry.invalid/'+k+'@sha256:'+'1'*64 for k in m.WORKLOADS},
         'expected_runtime_image_ids':{k:'containerd://sha256:'+'2'*64 for k in m.WORKLOADS}}
    if stage=='uat':c['merge_shas']=copy.deepcopy(c['source_shas'])
    o={k:copy.deepcopy(c[k]) for k in ('environment','stage','feature_id','namespace','cluster','instance_id','candidate_fingerprint','gitops_sha','origins','namespace_uid','node_uid','cluster_uid')}
    o.update(schema='referral-deployment-observation/v1',sampled_at=NOW.isoformat(),flux_ready=True,observed_generation_current=True,
             workloads={k:{'namespace':c['namespace'],'namespace_uid':c['namespace_uid'],'pod_uid':'pod-'+k,'ready':True,'image':c['images'][k],'image_id':c['expected_runtime_image_ids'][k]} for k in c['images']})
    return c,o


class LiveAcceptanceContract(unittest.TestCase):
    def test_real_probes_are_bound_but_never_full_business_pass(self):
        c,o=fixture();calls=[]
        def transport(url,method,payload,headers):
            calls.append((url,method,payload))
            if method=='POST':return 401,b'{"error":"invalid_signature"}'
            if url.endswith('/api/ping'):return 200, ('{"image":"'+c['images']['accounts']+'"}').encode()
            return 200,b'{}'
        r=m.assess(c,o,transport,NOW)
        self.assertEqual(r['result'],'BLOCKED');self.assertEqual(len(calls),4)
        for key in ('deployment_binding','accounts_ready','console_route','accounts_runtime_image','bad_signature_401'):
            self.assertEqual(r['checks'][key]['status'],'PASS')
        self.assertTrue(all(r['checks'][key]['status']=='NOT_RUN' for key in m.BUSINESS_CASES))

    def test_wrong_or_stale_scope_stops_before_network(self):
        for field,value in [('namespace','other'),('namespace_uid','44444444-4444-4444-4444-444444444444'),('instance_id','old-instance'),('candidate_fingerprint','f'*64),('sampled_at',(NOW-dt.timedelta(minutes=11)).isoformat()),('flux_ready',False)]:
            with self.subTest(field=field):
                c,o=fixture();o[field]=value
                r=m.assess(c,o,lambda *a:self.fail('must stop before HTTP'),NOW)
                self.assertEqual(r['checks']['deployment_binding']['status'],'BLOCKED')

    def test_changed_runtime_digest_and_unready_workload_block(self):
        for field,value in [('image_id','containerd://sha256:'+'3'*64),('ready',False),('namespace','other')]:
            c,o=fixture();o['workloads']['accounts'][field]=value
            self.assertEqual(m.assess(c,o,lambda *a:self.fail('must not probe'),NOW)['result'],'BLOCKED')

    def test_uat_requires_own_merged_source_and_runtime_binding(self):
        c,o=fixture('uat');m.validate(c,o,NOW)
        del c['merge_shas'];self.assertRaises(ValueError,m.validate,c,o,NOW)
        c,o=fixture('uat');o['stage']='sit';self.assertRaises(ValueError,m.validate,c,o,NOW)

    def test_bad_signature_503_or_wrong_body_is_failure(self):
        c,o=fixture()
        for status,body in [(503,b'{"error":"stripe_not_configured"}'),(401,b'{"error":"different"}')]:
            def transport(url,method,payload,headers):
                if method=='POST':return status,body
                if url.endswith('/api/ping'):return 200, ('{"image":"'+c['images']['accounts']+'"}').encode()
                return 200,b'{}'
            self.assertEqual(m.assess(c,o,transport,NOW)['checks']['bad_signature_401']['status'],'FAIL')

    def test_unsafe_origins_and_missing_candidate_are_rejected(self):
        for value in ('http://example.invalid','https://name:secret@example.invalid','https://example.invalid/path','https://example.invalid?token=x'):
            self.assertRaises(ValueError,m.origin,value)
        c,o=fixture();c['data_baseline']='unknown';self.assertRaises(ValueError,m.validate,c,o,NOW)

    def test_malformed_artifacts_block_without_network(self):
        for contract,observation in [(None,{}),([],{}),({},[])]:
            self.assertEqual(m.assess(contract,observation,lambda *a:self.fail('must not probe'),NOW)['result'],'BLOCKED')
        c,o=fixture();o['workloads']['accounts']=[]
        self.assertEqual(m.assess(c,o,lambda *a:self.fail('must not probe'),NOW)['result'],'BLOCKED')

if __name__=='__main__':unittest.main()

# Offline transport contract tests; these never count as deployed business evidence.
import sys
sys.path.insert(0,str(ROOT/'roles/referral_deployment_acceptance/files'))
import live_registration as live

class RegistrationContract(unittest.TestCase):
    def samples(self):
        return {role:{'name':'referral-offline-fixture-'+role,'email':'referral-offline-fixture-'+role+'@acceptance.onwalk.net','password':'isolated-password-'+role,'code':'123456'} for role in ('owner','invitee')}
    def health(self,c):
        def call(url,method,payload,headers):
            if url.endswith('/api/ping'):return 200,json.dumps({'image':c['images']['accounts']}).encode()
            if method=='POST':return 401,b'{"error":"invalid_signature"}'
            return 200,b'{}'
        return call
    def factory(self,calls,mfa=False):
        count=[0]
        def make(origin):
            count[0]+=1;role=count[0];registrations=[0]
            def call(path,method='GET',payload=None):
                calls.append((role,path))
                if path=='/api/referrals/campaign':return 200,{'campaign':{'active':True,'enabled':True,'cashbackBps':1000,'annualBonusDays':30}}
                if path=='/api/auth/register':
                    registrations[0]+=1
                    if registrations[0]>1:return 409,{'error':'email_already_exists'}
                    if role==2:self.assertEqual(payload['referralCode'],'OWNER123')
                    return 200,{'success':True}
                if path=='/api/auth/login':return 200,{'success':True,'needMfa':mfa}
                return 200,{'code':'OWNER123' if role==1 else 'INVITEE123'}
            return call
        return make
    def test_offline_registration_sequence_never_certifies_attribution_or_sandbox(self):
        c,o=fixture();calls=[]
        r=live.execute(c,o,self.samples(),self.factory(calls),NOW,self.health(c))
        self.assertEqual(r['result'],'BLOCKED');self.assertEqual(r['checks']['duplicate_registration_rejected'],'PASS')
        self.assertEqual(r['registration_attribution'],'BLOCKED');self.assertEqual(r['sandbox_acceptance'],'NOT_RUN')
        self.assertNotIn('isolated-password',json.dumps(r));self.assertNotIn('123456',json.dumps(r));self.assertEqual(len(calls),8)
    def test_wrong_namespace_uid_origin_or_runtime_stops_before_network(self):
        for key,value in [('namespace_uid','44444444-4444-4444-4444-444444444444'),('origins',{'accounts':'https://accounts.svc.plus','console':'https://console.svc.plus'})]:
            c,o=fixture();o[key]=value
            r=live.execute(c,o,self.samples(),lambda *a:self.fail('no HTTP'),NOW,lambda *a:self.fail('no HTTP'))
            self.assertEqual(r['result'],'BLOCKED');self.assertFalse(r['checks'])
    def test_missing_mailbox_code_or_unscoped_sample_blocks_before_http(self):
        for key,value in [('code',''),('email','existing@prod.svc.plus'),('name','root')]:
            c,o=fixture();samples=self.samples();samples['owner'][key]=value
            r=live.execute(c,o,samples,lambda *a:self.fail('no HTTP'),NOW,lambda *a:self.fail('no HTTP'))
            self.assertFalse(r['checks'])
    def test_mfa_challenge_blocks_without_disabling_or_skipping(self):
        c,o=fixture();calls=[];r=live.execute(c,o,self.samples(),self.factory(calls,True),NOW,self.health(c))
        self.assertEqual(r['result'],'BLOCKED');self.assertNotIn('owner_bff_session',r['checks']);self.assertFalse(any(role==2 for role,path in calls))
