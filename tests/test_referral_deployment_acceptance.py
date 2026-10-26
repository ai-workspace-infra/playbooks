import copy
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
         'sit_namespace':'feature-referral-sit','uat_namespace':'web-saas-uat','namespace':'feature-referral-sit' if stage=='sit' else 'web-saas-uat',
         'cluster':'dev-k3s','instance_id':'cloud-instance-verified','candidate_fingerprint':'a'*64,'gitops_sha':'b'*40,
         'owner_sha':'c'*40,'run_id':'offline-fixture','data_baseline':'isolated-seed-v1','source_shas':{'accounts':'d'*40,'portal':'e'*40},
         'origins':{'accounts':'https://accounts.feature.example.invalid','console':'https://console.feature.example.invalid'},
         'images':{k:'registry.invalid/'+k+'@sha256:'+'1'*64 for k in m.WORKLOADS},
         'expected_runtime_image_ids':{k:'containerd://sha256:'+'2'*64 for k in m.WORKLOADS}}
    if stage=='uat':c['merge_shas']=copy.deepcopy(c['source_shas'])
    o={k:copy.deepcopy(c[k]) for k in ('environment','stage','feature_id','namespace','cluster','instance_id','candidate_fingerprint','gitops_sha','origins')}
    o.update(schema='referral-deployment-observation/v1',sampled_at=NOW.isoformat(),flux_ready=True,observed_generation_current=True,
             workloads={k:{'namespace':c['namespace'],'ready':True,'image':c['images'][k],'image_id':c['expected_runtime_image_ids'][k]} for k in c['images']})
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
        for field,value in [('namespace','other'),('instance_id','old-instance'),('candidate_fingerprint','f'*64),('sampled_at',(NOW-dt.timedelta(minutes=11)).isoformat()),('flux_ready',False)]:
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
