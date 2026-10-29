"""Synthetic transport/runtime falsification; no live registry or cluster calls."""
import copy,hashlib,importlib.util,json,sys,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
O=load('oci',ROOT/'roles/vhosts/k3s_runtime_secret_sync/files/dev_oci.py')
G=load('registry',ROOT/'roles/vhosts/k3s_runtime_secret_sync/files/dev_registry.py')
R=load('runtime',ROOT/'roles/platform/dev_feature_flux/files/reconcile.py')
sys.path.insert(0,str(ROOT/'scripts'))
import run_dev_feature_flux as adapter

def encoded(obj):return json.dumps(obj,sort_keys=True).encode()
def digest(raw):return 'sha256:'+hashlib.sha256(raw).hexdigest()

class Response:
 def __init__(self,raw,reported=None):self.raw=raw;self.headers={'Docker-Content-Digest':reported} if reported else {}
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,size):return self.raw[:size]

class OCITests(unittest.TestCase):
 def test_tagged_registry_requires_only_read_packages(self):
  class Profile:
   def __init__(self,scopes):self.headers={'X-OAuth-Scopes':scopes}
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'{"login":"feature-user"}'
  for scopes,accepted in [('read:packages',True),('read:packages, repo',False),('',False)]:
   with self.subTest(scopes=scopes),patch.object(G.urllib.request,'urlopen',return_value=Profile(scopes)):
    if accepted:G.verify_readonly_registry('feature-user','synthetic')
    else:
     with self.assertRaises(ValueError):G.verify_readonly_registry('feature-user','synthetic')
 def fixture(self,duplicate=False,architecture='amd64',labels=True):
  config={'os':'linux','architecture':architecture,'config':{'Env':['DO_NOT_RETURN=synthetic-private-value'],'Labels':{'org.opencontainers.image.source':'https://github.com/ai-workspace-services/accounts','org.opencontainers.image.revision':'a'*40} if labels else {}}}
  cb=encoded(config);child=encoded({'config':{'digest':digest(cb)}})
  descriptor={'digest':digest(child),'platform':{'os':'linux','architecture':'amd64'}}
  root=encoded({'manifests':[descriptor,descriptor] if duplicate else [descriptor]})
  repo='ai-workspace-services/accounts';bodies={'https://ghcr.io/v2/'+repo+'/manifests/'+digest(root):root,'https://ghcr.io/v2/'+repo+'/manifests/'+digest(child):child,'https://ghcr.io/v2/'+repo+'/blobs/'+digest(cb):cb}
  class Opener:
   def open(self,req,timeout):
    if '/token?' in req.full_url:return Response(encoded({'token':'synthetic-bearer'}))
    body=bodies[req.full_url];return Response(body,req.full_url.rsplit('/',1)[1])
  return 'ghcr.io/'+repo+'@'+digest(root),Opener(),bodies,digest(child)
 def test_index_child_config_verified_and_only_safe_fields_returned(self):
  ref,opener,_,child=self.fixture()
  with patch.object(O.urllib.request,'build_opener',return_value=opener):result=O.inspect(ref,'fixture-user','fixture-token','ai-workspace-services/accounts','a'*40)
  self.assertEqual(result['platform_manifest_digest'],child);self.assertTrue(result['source_label_match']);self.assertTrue(result['revision_label_match'])
  self.assertNotIn('synthetic-private-value',json.dumps(result));self.assertNotIn('synthetic-bearer',json.dumps(result))
 def test_missing_labels_are_unknown_not_fabricated_matches(self):
  ref,opener,_,_=self.fixture(labels=False)
  with patch.object(O.urllib.request,'build_opener',return_value=opener):result=O.inspect(ref,'u','p','ai-workspace-services/accounts','a'*40)
  self.assertEqual(result['revision_label_match'],'UNKNOWN');self.assertEqual(result['source_label_match'],'UNKNOWN')
 def test_duplicate_platform_wrong_arch_and_tampered_blob_rejected(self):
  for kwargs in ({'duplicate':True},{'architecture':'arm64'},{}):
   ref,opener,bodies,_=self.fixture(**kwargs)
   if not kwargs:
    key=next(k for k in bodies if '/blobs/' in k);bodies[key]=b'{}'
   with patch.object(O.urllib.request,'build_opener',return_value=opener),self.assertRaises(ValueError):O.inspect(ref,'u','p','ai-workspace-services/accounts','a'*40)
 def test_oversized_metadata_and_unsafe_redirect_rejected(self):
  ref,opener,bodies,_=self.fixture();bodies[next(iter(bodies))]=b' '* (O.LIMIT+1)
  with patch.object(O.urllib.request,'build_opener',return_value=opener),self.assertRaises(ValueError):O.inspect(ref,'u','p','ai-workspace-services/accounts','a'*40)
  handler=O.SafeRedirect();req=urllib.request.Request('https://ghcr.io/v2/image/blobs/sha256:x',headers={'Authorization':'Bearer synthetic'})
  for url in ('http://ghcr.io/x','https://evil.invalid/x','https://ghcr.io:444/x','https://u:p@ghcr.io/x'):
   with self.assertRaises(ValueError):handler.redirect_request(req,None,302,'',{},url)
  changed=handler.redirect_request(req,None,302,'',{},'https://pkg-containers.githubusercontent.com/blob')
  self.assertIsNone(changed.get_header('Authorization'))

class RuntimeTests(unittest.TestCase):
 def fixture(self):
  config={'namespace':'feature-xconnect-referral-sit','namespace_uid':'3'*36,'candidate_sha':'a'*40,'node_uid':'1'*36,'cluster_uid':'2'*36,'manifest_sha256':'b'*64}
  context={**config,'instance_id':'109324752','source':{'repository':'ai-workspace-infra/platform-ops-toolkit','workflow_ref':'ai-workspace-infra/platform-ops-toolkit/.github/workflows/dev-feature-deploy.yml@refs/heads/main','workflow_sha':'c'*40,'owner_sha':'d'*40,'run_id':'123','run_attempt':'1'},'images':{},'sources':{}}
  for i,name in enumerate(G.IMAGES):
   context['images'][name]='ghcr.io/'+G.IMAGES[name]+'@sha256:'+str(i)*64
   context['sources'][name]={'repository':G.SOURCES[name],'source_sha':'e'*40,'build_run_id':'10'}
  def inspect(ref,*args):
   return {'image':ref,'manifest_digest':ref.split('@')[1],'platform_manifest_digest':'sha256:'+'f'*64,'config_digest':'sha256:'+'e'*64,'platform':'linux/amd64','source_label_match':True,'revision_label_match':True,'actual_revision':'e'*40}
  mapping=G.mapping(context,{'GHCR_USERNAME':'u','GHCR_TOKEN':'synthetic'},inspect)
  config['instance_id']=context['instance_id'];config['runtime_mapping']=mapping;pods=[]
  for name,row in mapping['images'].items():
   annotations={'release.svc.plus/source_repository':row['source_repository'],'release.svc.plus/source_sha':row['source_sha'],'release.svc.plus/image_digest':row['manifest_digest'],'release.svc.plus/build_run_id':row['build_run_id']}
   pods.append({'metadata':{'name':'web-saas-'+name,'uid':'uid-'+name,'namespace':config['namespace'],'labels':{'app.kubernetes.io/instance':'web-saas','app.kubernetes.io/component':name},'annotations':annotations},'spec':{'nodeName':'dev-k3s-v5','containers':[{'name':name,'image':row['image']}]},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'name':name,'ready':True,'state':{'running':{}},'imageID':'docker-pullable://'+row['image'].split('@')[0]+'@'+row['platform_manifest_digest']}]}})
  return config,context,{'items':pods}
 def test_amd64_child_and_root_image_ids_are_both_legitimate(self):
  config,_,pods=self.fixture();self.assertEqual(len(R.verify_runtime(config,pods)),6)
  pods['items'][0]['status']['containerStatuses'][0]['imageID']=pods['items'][0]['spec']['containers'][0]['image']
  self.assertEqual(len(R.verify_runtime(config,pods)),6)
 def test_wrong_image_id_spec_ready_extra_container_and_stale_proof_rejected(self):
  config,_,pods=self.fixture()
  mutations=[lambda p:p['items'][0]['status']['containerStatuses'][0].update(imageID='containerd://sha256:'+'a'*64),lambda p:p['items'][0]['spec']['containers'][0].update(image='ghcr.io/other/image@sha256:'+'f'*64),lambda p:p['items'][0]['status']['containerStatuses'][0].update(ready=False),lambda p:p['items'][0]['spec']['containers'].append({'name':'unreviewed','image':'other'})]
  for mutate in mutations:
   changed=copy.deepcopy(pods);mutate(changed)
   with self.assertRaises(ValueError):R.verify_runtime(config,changed)
  config['candidate_sha']='c'*40
  with self.assertRaises(ValueError):R.verify_runtime(config,pods)
 def test_prepare_receipt_binding_and_candidate_image_change_invalidates(self):
  config,context,_=self.fixture();mapping=config['runtime_mapping']
  receipt={**config,'schema_version':'dev-feature-flux/v1','result':'prepared','mode':'prepare','instance_id':'109324752','owner_sha':'d'*40,'run_id':'123','run_attempt':'1','registry':{'result':'configured','namespace':config['namespace'],'source_path':'kv/CICD/dev/ghcr','secret_name':'web-saas-registry','oci_mapping':mapping}}
  observation={'images':context['images'],'sources':context['sources']}
  self.assertEqual(adapter.validate_prepare_receipt(receipt,config,observation),mapping)
  observation=copy.deepcopy(observation);observation['images']['accounts']='ghcr.io/ai-workspace-services/accounts@sha256:'+'a'*64
  with self.assertRaises(ValueError):adapter.validate_prepare_receipt(receipt,config,observation)
  receipt['candidate_sha']='f'*40
  with self.assertRaises(ValueError):adapter.validate_prepare_receipt(receipt,config,{'images':context['images'],'sources':context['sources']})
 def test_receipt_from_previous_instance_cannot_cross_bind(self):
  config,context,_=self.fixture();mapping=config['runtime_mapping']
  receipt={**config,'schema_version':'dev-feature-flux/v1','result':'prepared','mode':'prepare','instance_id':'109277144','owner_sha':'d'*40,'run_id':'123','run_attempt':'1','registry':{'result':'configured','namespace':config['namespace'],'source_path':'kv/CICD/dev/ghcr','secret_name':'web-saas-registry','oci_mapping':mapping}}
  with self.assertRaises(ValueError):adapter.validate_prepare_receipt(receipt,config,{'images':context['images'],'sources':context['sources']})
 def test_registry_mapping_rejects_wrong_repo_and_mismatched_source_label(self):
  _,context,_=self.fixture();context['images']['accounts']='ghcr.io/evil/accounts@sha256:'+'a'*64
  with self.assertRaises(ValueError):G.mapping(context,{'GHCR_USERNAME':'u','GHCR_TOKEN':'p'},lambda *args:{})
  _,context,_=self.fixture()
  with self.assertRaises(ValueError):G.mapping(context,{'GHCR_USERNAME':'u','GHCR_TOKEN':'p'},lambda *args:{'source_label_match':False})
 def test_tagged_registry_requires_exact_scope_and_protected_caller_before_vault(self):
  _,context,_=self.fixture();repo='ai-workspace-infra/Platform-Orchestrator'
  context['namespace']='feature-sit-test-1483-sit'
  context['tagged_lane']={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'f'*64}
  context['source'].update(repository=repo,workflow_ref=repo+'/.github/workflows/feature-lane-deploy.yml@refs/heads/main')
  secret=G.build(context['namespace'],{'GHCR_USERNAME':'u','GHCR_TOKEN':'synthetic'},context['tagged_lane'])
  self.assertEqual(secret['metadata']['namespace'],context['namespace'])
  G.validate_context(context)
  command=G.remote_command(context['namespace'],context['namespace_uid'],context['tagged_lane'])
  self.assertIn('release.svc.plus/feature-tag',command)
  bad=copy.deepcopy(context);bad['source']['workflow_ref']=repo+'/.github/workflows/untrusted.yml@refs/heads/main'
  with patch.dict('os.environ',{'DEV_REGISTRY_NAMESPACE':bad['namespace'],'DEV_REGISTRY_HOST':'127.0.0.1','DEV_REGISTRY_CONTEXT':json.dumps(bad)},clear=True),patch.object(G.urllib.request,'urlopen') as vault:
   with self.assertRaises(ValueError):G.main()
   vault.assert_not_called()
  bad=copy.deepcopy(context);bad['namespace']='feature-other-sit'
  with self.assertRaises(ValueError):G.validate_context(bad)
  bad=copy.deepcopy(context);bad['tagged_lane']['feature_tag']='other-sit-test-1484'
  with self.assertRaises(ValueError):G.validate_context(bad)
 def test_tagged_prepare_receipt_binds_tag_fingerprint_and_namespace(self):
  config,context,_=self.fixture();repo='ai-workspace-infra/Platform-Orchestrator'
  lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'f'*64}
  config.update(namespace='feature-sit-test-1483-sit',tagged_lane=lane)
  context.update(namespace=config['namespace'],tagged_lane=lane)
  context['source'].update(repository=repo,workflow_ref=repo+'/.github/workflows/feature-lane-deploy.yml@refs/heads/main')
  def inspect(ref,*args):return {'image':ref,'manifest_digest':ref.split('@')[1],'platform_manifest_digest':'sha256:'+'f'*64,'config_digest':'sha256:'+'e'*64,'platform':'linux/amd64','source_label_match':True,'revision_label_match':True}
  mapping=G.mapping(context,{'GHCR_USERNAME':'u','GHCR_TOKEN':'synthetic'},inspect)
  receipt={**config,'schema_version':'dev-feature-flux/v1','result':'prepared','mode':'prepare','feature_tag':lane['feature_tag'],'candidate_sha256':lane['candidate_sha256'],'instance_id':'109324752','owner_sha':'d'*40,'run_id':'123','run_attempt':'1','registry':{'result':'configured','namespace':config['namespace'],'source_path':'kv/sit/platform/ghcr/feature-registry','secret_name':'web-saas-registry','oci_mapping':mapping}}
  observation={'images':context['images'],'sources':context['sources']}
  self.assertEqual(adapter.validate_prepare_receipt(receipt,config,observation),mapping)
  bad=copy.deepcopy(receipt);bad['registry']['source_path']='kv/CICD/dev/ghcr'
  with self.assertRaises(ValueError):adapter.validate_prepare_receipt(bad,config,observation)
  for field,value in [('feature_tag','other-sit-test-1483'),('candidate_sha256','a'*64),('namespace','feature-other-sit')]:
   bad=copy.deepcopy(receipt);bad[field]=value
   with self.subTest(field=field),self.assertRaises(ValueError):adapter.validate_prepare_receipt(bad,config,observation)
if __name__=='__main__':unittest.main()
