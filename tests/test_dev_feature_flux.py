"""Offline scope and state-machine tests; never runtime acceptance."""
import copy,hashlib,importlib.util,json,sys,unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import dev_feature_candidate as C
R=C.SCOPE

class Contracts(unittest.TestCase):
 def config(self):
  return {'environment':'sit','namespace':R.SCOPES['sit'][0],'node_name':R.NODE,'mode':'plan','candidate_sha':'a'*40,'platform_sha':'b'*40,'owner_sha':'c'*40,'node_uid':'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12,'cluster_uid':'2'*8+'-'+'2'*4+'-'+'2'*4+'-'+'2'*4+'-'+'2'*12,'manifest_sha256':'d'*64,'wait_seconds':10}
 def docs(self,stage='sit'):
  ns=R.SCOPES[stage][0];docs=[]
  repos={'caddy':'ai-workspace-infra/caddy-cloudflare','postgresql':'ai-workspace-services/postgresql','console':'ai-workspace-services/console','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/docs'}
  release={};paths={'caddy':('image',),'postgresql':('image',),'console':('singleContainer','image'),'accounts':('workload','image'),'billing':('workload','image'),'content':('workload','image')}
  for i,(key,path) in enumerate(paths.items()):
   target=release.setdefault(key,{})
   for field in path[:-1]:target=target.setdefault(field,{})
   target[path[-1]]='ghcr.io/'+repos[key]+'@sha256:'+str(i)*64
  source_repos={'caddy':'ai-workspace-infra/artifacts','postgresql':'ai-workspace-services/postgresql','console':'ai-workspace-services/portal','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/content-service'}
  for key,path in paths.items():
   image=release[key]
   for field in path:image=image[field]
   release[key]['podAnnotations']={'release.svc.plus/source_repository':source_repos[key],'release.svc.plus/source_sha':'a'*40,'release.svc.plus/image_digest':image.split('@')[1],'release.svc.plus/build_run_id':'123'}
   if key in ('accounts','billing','content'):release[key]['workload']['env']={'IMAGE':image}
  for name in sorted(C.CM):docs.append({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name,'namespace':ns},'data':{'release.yaml':yaml.safe_dump(release)} if name=='web-saas-fullstack-release' else {'values.yaml':'{}'} if name=='web-saas-fullstack-values' else {}})
  docs += [{'apiVersion':'source.toolkit.fluxcd.io/v1beta2','kind':'OCIRepository','metadata':{'name':'web-saas-fullstack-chart','namespace':ns},'spec':{'url':'oci://ghcr.io/ai-workspace-infra/distributed-web-saas-fullstack','ref':{'digest':R.CHART}}},{'apiVersion':'helm.toolkit.fluxcd.io/v2','kind':'HelmRelease','metadata':{'name':'distributed-web-saas-fullstack','namespace':ns},'spec':{'releaseName':'web-saas','targetNamespace':ns,'serviceAccountName':'lane-reconciler','chartRef':{'kind':'OCIRepository','name':'web-saas-fullstack-chart'},'valuesFrom':[{'kind':'ConfigMap','name':'web-saas-fullstack-values','valuesKey':'values.yaml'},{'kind':'ConfigMap','name':'web-saas-fullstack-release','valuesKey':'release.yaml'}]}}]
  for name in ('web-saas-http',):
   docs.append({'apiVersion':'networking.k8s.io/v1','kind':'Ingress','metadata':{'name':name,'namespace':ns,'annotations':{'kubernetes.io/ingress.class':'dev-web-saas'}},'spec':{'rules':[{'host':h,'http':{'paths':[{'backend':{'service':{'name':'web-saas-caddy-ingress','port':{'number':80}}}}]}} for h in sorted(R.DOMAINS[stage])]}})
  return docs
 def baseline(self,c):
  condition=[{'type':'Ready','status':'True'}]
  nodes={'items':[{'metadata':{'name':R.NODE,'uid':c['node_uid']},'status':{'conditions':condition}}]}
  cluster={'metadata':{'uid':c['cluster_uid']}}
  meta={'generation':1};status={'observedGeneration':1,'conditions':condition,'artifact':{'revision':'sha1:'+c['platform_sha']}}
  source={'metadata':meta,'spec':{'ref':{'commit':c['platform_sha']}},'status':status}
  root={'metadata':meta,'status':{**status,'lastAppliedRevision':'sha1:'+c['platform_sha']}}
  return nodes,cluster,source,root
 def test_exact_stage_host_chart_and_images(self):
  for stage in ('sit','uat'):
   observed=C.validate_documents(self.docs(stage),stage)
   self.assertEqual(set(observed['hosts']),R.DOMAINS[stage]);self.assertEqual(len(observed['images']),6)
 def test_tls_subpath_requires_only_its_fixed_resolver_and_onwalk_hosts(self):
  doc=copy.deepcopy(self.docs()[-1]);doc['metadata']['name']='web-saas-https'
  doc['metadata']['annotations']['traefik.ingress.kubernetes.io/router.tls.certresolver']='dev-http01'
  self.assertEqual(C.validate_documents([doc],'sit',True)['images'],{})
  doc['spec']['tls']=[{'secretName':'prod-certificate','hosts':list(R.DOMAINS['sit'])}]
  with self.assertRaises(ValueError):C.validate_documents([doc],'sit',True)
 def test_uat_source_watches_main_without_switching_platform_root(self):
  c=self.config();c.update(environment='uat',namespace='web-saas-uat')
  docs=R.documents(c)
  self.assertEqual(docs[-2]['spec']['ref'],{'branch':'main'})
  self.assertEqual(docs[-1]['spec']['targetNamespace'],'web-saas-uat')
 def test_cluster_secret_rbac_and_other_namespace_rejected(self):
  for kind in ('Secret','Namespace','ClusterRole','RoleBinding','Kustomization','GitRepository'):
   docs=self.docs()+[{'kind':kind,'metadata':{'name':'forbidden','namespace':R.SCOPES['sit'][0]}}]
   with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
  docs=self.docs();docs[0]['metadata']['namespace']='production'
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
 def test_production_domains_and_admin_helm_identity_rejected(self):
  docs=self.docs();docs[-1]['spec']['rules'][0]['host']='console.svc.plus'
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
  docs=self.docs();next(x for x in docs if x['kind']=='HelmRelease')['spec']['serviceAccountName']='admin'
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
 def test_mutable_chart_and_image_injection_rejected(self):
  docs=self.docs();next(x for x in docs if x['kind']=='OCIRepository')['spec']['ref']={'tag':'latest'}
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
  docs=self.docs();hr=next(x for x in docs if x['kind']=='HelmRelease');hr['spec']['postRenderers']=[{'kustomize':{'patches':[{'target':{'kind':'Deployment','name':'web-saas-accounts'},'patch':'spec:\n  image: busybox:latest'}]}}]
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
 def test_generated_bootstrap_never_changes_platform_root(self):
  docs=R.documents(self.config());self.assertEqual(docs[0]['kind'],'Namespace')
  self.assertFalse(any(x['kind'] in ('ClusterRole','ClusterRoleBinding','Secret') for x in docs))
  self.assertTrue(all(x['metadata'].get('namespace')==R.SCOPES['sit'][0] for x in docs[1:]))
  k=docs[-1]['spec'];self.assertEqual(k['serviceAccountName'],'lane-reconciler');self.assertFalse(k['prune'])
  role=next(x for x in docs if x['kind']=='Role')
  self.assertFalse(any('*' in rule['resources'] or 'roles' in rule['resources'] for rule in role['rules']))
 def test_cluster_and_platform_drift_block_before_mutation(self):
  c=self.config();base=self.baseline(c);R.preflight(c,*base)
  base[1]['metadata']['uid']='wrong'
  with self.assertRaises(ValueError):R.preflight(c,*base)
  base=self.baseline(c);base[2]['spec']['ref']={'branch':'main'}
  with self.assertRaises(ValueError):R.preflight(c,*base)
 def test_plan_is_read_only_and_prepare_does_not_deploy_candidate(self):
  c=self.config();base=self.baseline(c)
  class Client:
   def __init__(self):self.applied=[]
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace':return base[1] if name=='kube-system' else ({'metadata':{'uid':'3'*36}} if self.applied else None)
    return base[2] if kind=='gitrepository' else base[3]
   def apply(self,docs):self.applied=docs
  client=Client();self.assertEqual(R.execute(c,client)['result'],'planned');self.assertFalse(client.applied)
  c['mode']='prepare';self.assertEqual(R.execute(c,client)['result'],'prepared');self.assertEqual(len(client.applied),7)
 def test_namespace_takeover_and_prod_scope_rejected(self):
  c=self.config();c['environment']='prod'
  with self.assertRaises(ValueError):R.validate(c)
  c=self.config();c['namespace']='feature-other-sit'
  with self.assertRaises(ValueError):R.validate(c)


class PhaseTests(unittest.TestCase):
 config=Contracts.config
 baseline=Contracts.baseline
 def test_submit_is_pending_and_verify_requires_bound_seed(self):
  c=self.config();base=self.baseline(c)
  class Client:
   def __init__(self):self.applied=[]
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace':return base[1] if name=='kube-system' else ({'metadata':{'uid':'3'*36}} if self.applied else None)
    return base[2] if kind=='gitrepository' else base[3]
   def apply(self,docs):self.applied.append(docs)
  c['mode']='submit';client=Client()
  result=R.execute(c,client)
  self.assertEqual(result['result'],'submitted_pending_seed');self.assertEqual(result['business_acceptance'],'NOT_RUN')
  c['mode']='verify';client=Client()
  with self.assertRaises(ValueError):R.execute(c,client)
  self.assertEqual(client.applied,[])

class RegistryProjectionTests(unittest.TestCase):
 def test_only_fixed_namespace_and_registry_secret(self):
  import importlib.util
  path=ROOT/'roles/vhosts/k3s_runtime_secret_sync/files/dev_registry.py'
  spec=importlib.util.spec_from_file_location('dev_registry',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  obj=module.build('feature-xconnect-referral-sit',{'GHCR_USERNAME':'fixture','GHCR_TOKEN':'synthetic'})
  self.assertEqual(obj['metadata']['name'],'web-saas-registry')
  self.assertEqual(obj['type'],'kubernetes.io/dockerconfigjson')
  for ns in ('production','default','flux-system'):
   with self.assertRaises(ValueError):module.build(ns,{'GHCR_USERNAME':'fixture','GHCR_TOKEN':'synthetic'})
  with self.assertRaises(ValueError):module.build('web-saas-uat',{})

class CandidateEvolutionTests(unittest.TestCase):
 def test_two_pr_heads_and_changed_images_use_same_owner_policy(self):
  import run_dev_feature_flux as owner
  for sha,pr in [('a'*40,605),('b'*40,606)]:
   receipt={'schema_version':'dev-feature-candidate/v1','result':'verified','repository':'ai-workspace-infra/gitops','stage':'sit','candidate_head':sha,'candidate_pr':pr,'base_ref':'main','base_sha':'c'*40,'checks':'success','same_repository':True,'main_ancestry_verified':True,'source':{'workflow_ref':'ai-workspace-infra/platform-ops-toolkit/.github/workflows/dev-feature-deploy.yml@refs/heads/main','run_id':'123'}}
   self.assertEqual(owner.validate_candidate_receipt(receipt,sha,'sit','123')['candidate_head'],sha)
   with self.assertRaises(ValueError):owner.validate_candidate_receipt(receipt,'d'*40,'sit','123')
   with self.assertRaises(ValueError):owner.validate_candidate_receipt(receipt,sha,'sit','different-run')
  docs=Contracts().docs();first=C.validate_documents(docs,'sit')['images']['accounts']
  release_doc=next(x for x in docs if x['metadata']['name']=='web-saas-fullstack-release')
  release=yaml.safe_load(release_doc['data']['release.yaml'])
  image='ghcr.io/ai-workspace-services/accounts@sha256:'+'f'*64
  release['accounts']['workload']['image']=image;release['accounts']['workload']['env']['IMAGE']=image
  release['accounts']['podAnnotations']['release.svc.plus/image_digest']='sha256:'+'f'*64
  release['accounts']['podAnnotations']['release.svc.plus/source_sha']='e'*40
  release['accounts']['podAnnotations']['release.svc.plus/build_run_id']='456'
  release_doc['data']['release.yaml']=yaml.safe_dump(release)
  self.assertNotEqual(first,C.validate_documents(docs,'sit')['images']['accounts'])
  release['accounts']['podAnnotations']['release.svc.plus/image_digest']='sha256:'+'0'*64
  release_doc['data']['release.yaml']=yaml.safe_dump(release)
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')


class PrivilegeScopeTests(unittest.TestCase):
 def test_component_secrets_and_overlay_order_cannot_escalate(self):
  C.validate_values({'accounts':{'workload':{'secretRefs':['web-saas-accounts-runtime']}},'postgresql':{'existingSecret':'web-saas-db-admin'},'global':{'imagePullSecrets':[{'name':'web-saas-registry'}]}})
  for value in [{'accounts':{'workload':{'secretRefs':['web-saas-db-admin']}}},{'accounts':{'workload':{'secretKeyRef':{'name':'web-saas-registry'}}}},{'accounts':{'workload':{'serviceAccountName':'lane-reconciler'}}},{'extraObjects':[{'kind':'Secret'}]}]:
   with self.assertRaises(ValueError):C.validate_values(value)
  docs=Contracts().docs();hr=next(x for x in docs if x['kind']=='HelmRelease');hr['spec']['valuesFrom'].reverse()
  with self.assertRaises(ValueError):C.validate_documents(docs,'sit')
 def test_post_renderer_cannot_mount_admin_secret_or_management_identity(self):
  for patch in [{'spec':{'template':{'spec':{'serviceAccountName':'lane-reconciler'}}}},[{'op':'add','path':'/spec/template/spec/automountServiceAccountToken','value':True}],{'spec':{'template':{'spec':{'volumes':[{'secret':{'secretName':'web-saas-db-admin'}}]}}}}]:
   with self.assertRaises(ValueError):C.validate_patch_scope(patch)


class NamespaceIncarnationTests(unittest.TestCase):
 def test_same_name_new_uid_rejects_old_verified_context(self):
  c=Contracts().config();c.update(mode='verify',namespace_uid='3'*36);base=Contracts().baseline(c)
  class Client:
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace':
     if name=='kube-system':return base[1]
     return {'metadata':{'uid':'4'*36,'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','release.svc.plus/feature':'xconnect-referral','environment':'sit'}}}
    return base[2] if kind=='gitrepository' else base[3]
   def apply(self,docs):raise AssertionError('stale namespace must fail before apply')
  with self.assertRaises(ValueError):R.execute(c,Client())

if __name__=='__main__':unittest.main()
