import copy,hashlib,json,sys,unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import dev_platform_candidate as C
S=C.S;R=S.R
SOURCE=list(yaml.safe_load_all((ROOT/'tests/fixtures/dev_platform_ingress_source.yaml').read_text()))
CHART=list(yaml.safe_load_all((ROOT/'tests/fixtures/dev_platform_ingress_chart.yaml').read_text()))

def config(mode='plan'):
 return {'mode':mode,'environment':'dev','namespace':S.NS,'node_name':R.NODE,'node_uid':'11111111-1111-1111-1111-111111111111','cluster_uid':'22222222-2222-2222-2222-222222222222','candidate_sha':'a'*40,'platform_sha':'b'*40,'owner_sha':'c'*40,'manifest_sha256':S.MANIFEST,'wait_seconds':10}

def pod():
 d=next(copy.deepcopy(x) for x in CHART if x['kind']=='Deployment');p=d['spec']['template'];p['metadata'].update(uid='pod-uid',namespace=S.NS)
 p['spec']['nodeName']=R.NODE;p['status']={'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'ready':True,'imageID':S.IMAGE.split('@')[0]+'@'+S.AMD64}]};return p

class Client:
 def __init__(self,c):self.c=c;self.applied=[];self.foreign=False;self.root_drift=False
 def get(self,kind,name=None,namespace=None,optional=False):
  c=self.c;ready={'observedGeneration':1,'conditions':[{'type':'Ready','status':'True'}]};meta={'generation':1}
  if kind=='nodes':return {'items':[{'metadata':{'name':R.NODE,'uid':c['node_uid']},'status':{'nodeInfo':{'architecture':'amd64'},'conditions':ready['conditions']}}]}
  if kind=='namespace':
   if name=='kube-system':return {'metadata':{'uid':'drift' if self.root_drift else c['cluster_uid']}}
   doc=next(copy.deepcopy(d) for d in S.documents(c) if d['kind']=='Namespace' and d['metadata']['name']==name)
   if self.foreign:doc['metadata']['labels']['app.kubernetes.io/managed-by']='other-owner'
   return doc
  if kind=='gitrepository':
   if namespace=='flux-system':return {'metadata':meta,'spec':{'ref':{'commit':c['platform_sha']}},'status':{**ready,'artifact':{'revision':'sha1:'+c['platform_sha']}}}
   return {'metadata':meta,'spec':{'url':R.REPO,'ref':{'commit':c['candidate_sha']}},'status':{**ready,'artifact':{'revision':'sha1:'+c['candidate_sha']}}}
  if kind=='kustomization':
   if namespace=='flux-system':return {'metadata':meta,'status':{**ready,'lastAppliedRevision':'sha1:'+c['platform_sha']}}
   return {'metadata':meta,'spec':S.documents(c)[-1]['spec'],'status':{**ready,'lastAppliedRevision':'sha1:'+c['candidate_sha']}}
  if kind=='helmrelease':return {'metadata':meta,'spec':next(d for d in SOURCE if d['kind']=='HelmRelease')['spec'],'status':ready}
  if kind=='ocirepository':return next(d for d in SOURCE if d['kind']=='OCIRepository')
  if kind=='configmap':return next(d for d in SOURCE if d['kind']=='ConfigMap')
  if kind=='pods':return {'items':[pod()] if namespace else []}
  if kind in ('role','rolebinding'):return next(d for d in S.documents(c) if d['kind'].lower()==kind and d['metadata']['namespace']==namespace and d['metadata']['name']==name)
  raise AssertionError((kind,name,namespace))
 def apply(self,docs):self.applied.append(copy.deepcopy(docs))

class Platform(unittest.TestCase):
 def test_exact_reviewed_source_and_chart_render_resources(self):
  self.assertEqual(C.validate_documents(SOURCE)['chart_digest'],S.CHART)
  self.assertFalse(any(x['kind'] in ('ClusterRole','ClusterRoleBinding','CustomResourceDefinition') for x in CHART))
  self.assertEqual({x['metadata'].get('namespace') for x in CHART},set([S.NS,*S.WATCH]))
  self.assertEqual(len([x for x in CHART if x['kind']=='Deployment']),1)
 def test_arbitrary_platform_content_image_or_privilege_hash_cannot_be_self_approved(self):
  for change in (lambda d:d.append({'kind':'ClusterRole','metadata':{'name':'admin'}}),lambda d:d[0].update(data={'values.yaml':'hostNetwork: true'})):
   docs=copy.deepcopy(SOURCE);change(docs)
   with self.assertRaises(ValueError):C.validate_documents(docs)
  c=config();c['manifest_sha256']='f'*64
  with self.assertRaises(ValueError):S.execute(c,Client(c))
 def test_prepare_namespaces_precede_namespaced_chart_rbac_without_apps(self):
  c=config();docs=S.documents(c)
  self.assertEqual([d['metadata']['name'] for d in docs[:3]],S.WATCH+[S.NS])
  for d in docs:
   self.assertNotIn(d['kind'],('ClusterRole','ClusterRoleBinding','CustomResourceDefinition','Deployment','Secret'))
   if d['kind']!='Namespace':self.assertIn(d['metadata']['namespace'],[S.NS,*S.WATCH])
  for stage,(name,_,_) in R.SCOPES.items():
   expected=R.documents({**S.foundation(c),'environment':stage,'namespace':name})[0]
   self.assertEqual(next(d for d in docs if d['kind']=='Namespace' and d['metadata']['name']==name),expected)
  for stage,(name,_,_) in R.SCOPES.items():
   expected=R.namespace_documents({**S.foundation(c),'environment':stage,'namespace':name})
   for identity in expected:self.assertIn(identity,docs)
   auditor=next(d for d in expected if d['kind']=='Role' and d['metadata']['name']=='lane-auditor')
   for rule in auditor['rules']:
    self.assertFalse({'secrets','configmaps','pods/exec','pods/log','serviceaccounts/token'} & set(rule['resources']))
    self.assertTrue(set(rule['verbs']) <= {'get','list','watch'})
  self.assertEqual(docs[-1]['spec']['patches'][0]['patch'].splitlines()[-1],'$patch: delete')
 def test_controller_rbac_covers_exact_rendered_watch_roles_without_cluster_escalation(self):
  docs=S.documents(config())
  for role in [d for d in docs if d['kind']=='Role' and d['metadata']['name']==S.SA]:
   ns=role['metadata']['namespace'];rules=role['rules']
   for r in rules:
    self.assertNotIn('*',r['apiGroups']+r['resources']+r['verbs']);self.assertFalse({'bind','escalate','impersonate'}&set(r['verbs']))
    if ns!=S.NS and ({'deployments','statefulsets'}&set(r['resources'])):self.fail('no app deployment authority')
    if ns!=S.NS and 'secrets' in r['resources']:self.assertFalse({'create','update','patch','delete'}&set(r['verbs']))
   actual=next(d for d in CHART if d['kind']=='Role' and d['metadata']['namespace']==ns)
   for r in actual['rules']:
    for group in r['apiGroups']:
     for resource in r['resources']:
      for verb in r['verbs']:self.assertTrue(any(group in allowed['apiGroups'] and resource in allowed['resources'] and verb in allowed['verbs'] for allowed in rules))
 def test_plan_has_no_cluster_mutation_and_reconcile_verifies_only_ingress(self):
  c=config();client=Client(c);self.assertEqual(S.execute(c,client)['result'],'planned');self.assertFalse(client.applied)
  c=config('reconcile');client=Client(c);result=S.execute(c,client,http_probe=lambda:404)
  self.assertEqual(result['result'],'verified');self.assertEqual(result['host_ports'],[80,443]);self.assertEqual(result['watch_namespaces'],S.WATCH)
  self.assertEqual(result['tls_certificate_acceptance'],'NOT_RUN');self.assertEqual(len(client.applied),1)
 def test_wrong_namespace_existing_owner_and_root_drift_block_before_mutation(self):
  for key,value in [('namespace','production'),('environment','prod'),('node_uid','wrong')]:
   c=config();c[key]=value;client=Client(c)
   with self.assertRaises(ValueError):S.execute(c,client)
   self.assertFalse(client.applied)
  for field in ('foreign','root_drift'):
   c=config();client=Client(c);setattr(client,field,True)
   with self.assertRaises(ValueError):S.execute(c,client)
   self.assertFalse(client.applied)
 def test_runtime_privileged_hostnetwork_wrong_digest_or_hostport_rejected(self):
  changes=[lambda p:p['spec'].update(hostNetwork=True),lambda p:p['spec']['containers'][0]['securityContext'].update(privileged=True),lambda p:p['spec']['containers'][0]['ports'].append({'hostPort':22,'containerPort':22}),lambda p:p['status']['containerStatuses'][0].update(imageID='repo@sha256:'+'f'*64),lambda p:p['spec'].update(nodeName='production')]
  for change in changes:
   p=pod();change(p)
   with self.assertRaises(ValueError):S.verify_pod(p)
 def test_actual_reviewed_platform_imageID_and_class(self):
  result=S.verify_pod(pod());self.assertEqual(result['ingress_class'],'dev-web-saas')
  p=pod();p['spec']['containers'][0]['args']=[a for a in p['spec']['containers'][0]['args'] if 'namespaces=' not in a]
  with self.assertRaises(ValueError):S.verify_pod(p)

if __name__=='__main__':unittest.main()
