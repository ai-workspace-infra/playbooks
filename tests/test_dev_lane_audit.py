"""Offline audit-identity/UID falsification; not a runtime acceptance receipt."""
import base64,importlib.util,io,json,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
A=load('audit',ROOT/'roles/platform/dev_feature_flux/files/audit.py')
R=load('scope',ROOT/'roles/platform/dev_feature_flux/files/reconcile.py')

class AuditPolicyTests(unittest.TestCase):
 def test_prepare_has_independent_readonly_identity_without_sensitive_resources(self):
  config={'environment':'sit','mode':'prepare','candidate_sha':'a'*40}
  docs=R.namespace_documents(config);self.assertEqual(len(docs),7)
  sa=next(d for d in docs if d['kind']=='ServiceAccount' and d['metadata']['name']=='lane-auditor')
  self.assertFalse(sa['automountServiceAccountToken'])
  role=next(d for d in docs if d['kind']=='Role' and d['metadata']['name']=='lane-auditor')
  self.assertTrue(all(set(rule['verbs'])=={'get','list','watch'} for rule in role['rules']))
  self.assertEqual({v for rule in role['rules'] for v in rule['resources']},{'pods','services','deployments','statefulsets','replicasets'})
  self.assertFalse(any(d['kind'].startswith('Cluster') for d in docs))
 def test_token_subject_ttl_and_no_token_in_public_results(self):
  def token(sub,exp):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sub':sub,'exp':exp}).encode()).decode().rstrip('=')+'.signature'
  subject='system:serviceaccount:feature-xconnect-referral-sit:lane-auditor'
  self.assertEqual(A.token_claims(token(subject,1600),'feature-xconnect-referral-sit',1000)['sub'],subject)
  for sub,exp in [('system:admin',1600),(subject,5000),(subject,900)]:
   with self.assertRaises(ValueError):A.token_claims(token(sub,exp),'feature-xconnect-referral-sit',1000)
 def test_actual_pid1_uid_is_separate_from_static_run_as_nonroot(self):
  text='Name:\tfixture\nPid:\t1\nUid:\t65532\t65532\t65532\t65532\nPrivate:\tsynthetic-do-not-return\n'
  result=A.process_uid(text);self.assertEqual(result['effective_uid'],65532);self.assertNotIn('synthetic',json.dumps(result))
  with self.assertRaises(ValueError):A.process_uid(text.replace('65532','0'))
  with self.assertRaises(ValueError):A.process_uid(text.replace('Pid:\t1','Pid:\t20'))

class HTTPTests(unittest.TestCase):
 def test_ca_comes_only_from_fixed_kubeconfig_public_data(self):
  ca='-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----\n'
  with patch.object(A,'command',return_value=base64.b64encode(ca.encode()).decode()) as command, patch.object(A.ssl,'create_default_context') as context:
   A.API('synthetic')
  self.assertEqual(command.call_args.args[0],['config','view','--minify','--raw','-o','jsonpath={.clusters[0].cluster.certificate-authority-data}'])
  context.assert_called_once_with(cadata=ca)
  for encoded in ('','not-base64!', 'a'*32_001):
   with patch.object(A,'command',return_value=encoded):
    with self.assertRaises((ValueError, A.base64.binascii.Error)):A.API('synthetic')
 def test_created_is_accepted_only_when_requested(self):
  class Response:
   status=201
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'{"status":{"userInfo":{"username":"synthetic"}}}'
  class Opener:
   def open(self,req,timeout):return Response()
  api=object.__new__(A.API);api.token='synthetic';api.opener=Opener()
  path='/apis/authentication.k8s.io/v1/selfsubjectreviews'
  self.assertEqual(api.request(path,'POST',{},expected=201)['status']['userInfo']['username'],'synthetic')
  with self.assertRaises(ValueError):api.request('/api/v1/namespaces/feature-xconnect-referral-sit/pods')
  with self.assertRaises(ValueError):api.request('/api/v1/namespaces/feature-xconnect-referral-sit/secrets/x',expected=403,read=False)
 def test_wrong_secret_permission_never_reads_body(self):
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):raise AssertionError('sensitive body must not be read')
  class Opener:
   def open(self,req,timeout):return Response()
  api=object.__new__(A.API);api.token='synthetic';api.opener=Opener()
  with self.assertRaises(ValueError):api.request('/api/v1/namespaces/feature-xconnect-referral-sit/secrets/x',expected=403,read=False)
 def test_forbidden_response_is_recorded_without_body_and_redirects_fail(self):
  class Body(io.BytesIO):
   def read(self,*args):raise AssertionError('forbidden body must not be read')
  class Opener:
   def open(self,req,timeout):raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},Body(b'synthetic-private'))
  api=object.__new__(A.API);api.token='synthetic';api.opener=Opener()
  self.assertEqual(api.request('/api/v1/namespaces/feature-xconnect-referral-sit/secrets/x',expected=403,read=False),{'status':403})
  with self.assertRaises(ValueError):A.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid')

class ActualIdentityFlowTests(unittest.TestCase):
 def test_actual_auditor_requests_and_management_uid_probes_are_distinct(self):
  ns='feature-xconnect-referral-sit';config={'environment':'sit','namespace':ns,'namespace_uid':'3'*36,'mode':'verify'}
  rows={name:{'pod':'web-saas-'+name,'pod_uid':'uid-'+name} for name in A.NAMES}
  runtime={'result':'verified','namespace_uid':'3'*36,'runtime_images':rows};calls=[]
  class API:
   def request(self,path,method='GET',body=None,expected=200,content_type=None,read=True):
    calls.append((method,path,expected,read))
    if path.endswith('selfsubjectreviews'):return {'status':{'userInfo':{'username':'system:serviceaccount:'+ns+':lane-auditor'}}}
    if expected==403:return {'status':403}
    if path.endswith('/pods'):return {'items':[{'metadata':{'name':v['pod'],'uid':v['pod_uid']}} for v in rows.values()]}
    return {'items':[]}
  commands=[]
  def run(args):
   commands.append(args);self.assertNotIn('--token',args)
   if args[:2]==['get','namespace']:return '3'*36
   if args[0]=='get':return next(v['pod_uid'] for v in rows.values() if v['pod']==args[2])
   self.assertEqual(args[-2:],['/bin/cat','/proc/1/status'])
   return 'Pid:\t1\nUid:\t1000\t1000\t1000\t1000\n'
  result=A.execute(config,runtime,api=API(),run=run)
  self.assertEqual(result['audit_acceptance'],'verified');self.assertEqual(len(result['business_process_uid']),6)
  self.assertEqual(result['k3s_runtime_rootless'],'NOT_CLAIMED')
  self.assertTrue(any(method=='PATCH' and 'dryRun=All' in path and expected==403 for method,path,expected,_ in calls))
  self.assertTrue(any('/exec?' in path and expected==403 and not read for _,path,expected,read in calls))
  self.assertTrue(any(path.endswith('selfsubjectreviews') and expected==201 for _,path,expected,_ in calls))
  self.assertFalse(any('can-i' in args for args in commands))
if __name__=='__main__':unittest.main()
