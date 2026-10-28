"""Offline scope and state-machine tests; never runtime acceptance."""
import copy,hashlib,importlib.util,json,sys,unittest
from unittest.mock import patch,Mock
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import dev_feature_candidate as C
R=C.SCOPE

class Contracts(unittest.TestCase):
 def test_content_and_billing_need_real_readiness_probe(self):
  for component in ('content','billing'):
   R.verify_tagged_readiness_probe(component,{'readinessProbe':{'httpGet':{'path':'/readyz','port':8080}}})
   for probe in ({}, {'httpGet':{'path':'/healthz','port':8080}}, {'httpGet':{'path':'/readyz','port':8080,'host':'external.example'}}, {'httpGet':{'path':'/readyz','port':8080,'httpHeaders':[{'name':'Host','value':'other.example'}]}}):
    with self.subTest(component=component,probe=probe),self.assertRaisesRegex(ValueError,'readiness probe'):
     R.verify_tagged_readiness_probe(component,{'readinessProbe':probe})
 def test_tagged_public_probes_require_exact_dns_and_https_200(self):
  import run_dev_feature_flux as owner
  lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'e'*64}
  connection=Mock();connection.getresponse.return_value.status=200
  with patch.object(owner.socket,'gethostbyname_ex',return_value=('',[],['8.8.8.8'])), patch.object(owner.http.client,'HTTPSConnection',return_value=connection):
   result=owner.probe_tagged_public_https(lane,'8.8.8.8');self.assertEqual(len(result),3)
   self.assertEqual(connection.request.call_count,3)
   with patch.object(owner.socket,'create_connection') as connect:
    connection._create_connection(('other.example',443),10,None)
    connect.assert_called_once_with(('8.8.8.8',443),10,None)
   connection.getresponse.return_value.status=302
   with self.assertRaisesRegex(ValueError,'readiness failed'):owner.probe_tagged_public_https(lane,'8.8.8.8')
  with patch.object(owner.socket,'gethostbyname_ex',return_value=('',[],['8.8.4.4'])):
   with self.assertRaisesRegex(ValueError,'DNS target differs'):owner.probe_tagged_public_https(lane,'8.8.8.8')
 def config(self):
  return {'instance_id':'109324752','environment':'sit','namespace':R.SCOPES['sit'][0],'node_name':R.NODE,'mode':'plan','candidate_sha':'a'*40,'platform_sha':'b'*40,'owner_sha':'c'*40,'node_uid':'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12,'cluster_uid':'2'*8+'-'+'2'*4+'-'+'2'*4+'-'+'2'*4+'-'+'2'*12,'manifest_sha256':'d'*64,'wait_seconds':10}
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

 def test_tagged_feature_uses_its_own_namespace_path_and_owner(self):
  c=self.config();c['namespace']='feature-sit-test-1483-sit'
  c['tagged_lane']={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'e'*64}
  R.validate(c);ns,source,path,feature,domains,tag=R.scope(c)
  self.assertEqual((ns,source,feature,tag),('feature-sit-test-1483-sit','feature-sit-test-1483-sit','sit-test-1483','smoke-sit-test-1483'))
  self.assertEqual(path,'./services/distributed-web-saas-fullstack/dev/feature-lanes/sit-test-1483')
  self.assertEqual(domains,{'smoke-sit-test-1483-sit.onwalk.net','accounts-smoke-sit-test-1483-sit.onwalk.net','billing-smoke-sit-test-1483-sit.onwalk.net'})
  docs=R.documents(c)
  self.assertEqual(docs[0]['metadata']['name'],ns)
  self.assertEqual(docs[0]['metadata']['labels']['release.svc.plus/feature-tag'],tag)
  self.assertTrue(all(x['metadata'].get('namespace')==ns for x in docs[1:]))
  self.assertEqual(docs[-1]['spec']['path'],path)
  self.assertEqual(docs[-2]['spec']['ref'],{'commit':c['candidate_sha']})
  self.assertFalse(any(x['kind'] in ('ClusterRole','ClusterRoleBinding','Secret') for x in docs))
  for change in ({'feature_tag':'smoke-other-1483'}, {'feature_id':'../prod'}, {'candidate_sha256':'latest'}):
   bad=copy.deepcopy(c);bad['tagged_lane'].update(change)
   with self.subTest(change=change),self.assertRaises(ValueError):R.validate(bad)
  bad=copy.deepcopy(c);bad['mode']='rebuild';bad['namespace_uid']='1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12
  with self.assertRaisesRegex(ValueError,'rebuild is not authorized'):R.validate(bad)
  bad=copy.deepcopy(c);bad['mode']='enable-tls'
  with self.assertRaisesRegex(ValueError,'no separate TLS'):R.validate(bad)
  bad=copy.deepcopy(c);bad['namespace']='feature-another-sit'
  with self.assertRaises(ValueError):R.validate(bad)
  base=self.baseline(c)
  class Occupied:
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace' and name=='kube-system':return base[1]
    if kind=='namespace':return {'metadata':{'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','environment':'sit','release.svc.plus/feature':'sit-test-1483','feature_id':'sit-test-1483','release.svc.plus/feature-tag':'other-sit-test-1483'}}}
    return base[2] if kind=='gitrepository' else base[3]
  with self.assertRaisesRegex(ValueError,'no takeover'):R.execute(c,Occupied())

 def test_tagged_rendered_manifests_match_candidate_and_cannot_escape(self):
  docs=self.docs();old=C.validate_documents(docs,'sit')
  lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'e'*64}
  ns,_,_,_,hosts,_=R.scope({'environment':'sit','tagged_lane':lane})
  candidate={'tagged_lane':lane,'chart_digest':'sha256:'+'f'*64,'knowledge':{'repository':'ai-workspace-services/knowledge','sha':'a'*40},
             'components':{name:{'image':image,**old['sources'][name]} for name,image in old['images'].items()}}
  docs.insert(0,{'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'lane-isolation','namespace':ns},'spec':{'podSelector':{},'policyTypes':['Ingress','Egress'],'ingress':[{'from':[{'podSelector':{}}]}],'egress':[{'to':[{'podSelector':{}}]},{'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}],'ports':[{'protocol':'UDP','port':53},{'protocol':'TCP','port':53}]}]}})
  ranges=json.loads((ROOT/'scripts/github_git_cidrs.json').read_text())['git']
  docs.insert(1,{'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'content-github-egress','namespace':ns},'spec':{'podSelector':{'matchLabels':{'websaas.io/workload':'content'}},'policyTypes':['Egress'],'egress':[{'to':[{'ipBlock':{'cidr':cidr}} for cidr in ranges],'ports':[{'protocol':'TCP','port':443}]}]}})
  for doc in docs:doc['metadata']['namespace']=ns
  next(x for x in docs if x['kind']=='HelmRelease')['spec']['targetNamespace']=ns
  next(x for x in docs if x['kind']=='OCIRepository')['spec']['ref']={'digest':candidate['chart_digest']}
  caddy_patch={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'web-saas-caddy-config'},'data':{'Caddyfile':C.tagged_caddyfile(lane['feature_tag'])}}
  ingress_patch=[{'op':'replace','path':'/spec/ingress','value':[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'platform-ingress'}},'podSelector':{'matchLabels':{'app.kubernetes.io/name':'caddy-ingress-controller'}}},{'podSelector':{}}],'ports':[{'protocol':'TCP','port':8080}]}]}]
  tagged_values={name:{'enabled':True} for name in ('caddy','postgresql','console','accounts','billing','content')};tagged_values['caddy']['httpOnly']=True;tagged_values['content']['workload']={'env':{'KNOWLEDGE_REPO_URL':'https://github.com/ai-workspace-services/knowledge.git','KNOWLEDGE_REPO_REF':'a'*40}}
  tagged_values['accounts']['workload']={'env':{'DB_HOST':'web-saas-postgresql.'+ns+'.svc.cluster.local','DB_PORT':'5432','DB_NAME':'account','POSTGRES_USER':'xc_accounts_rw','DATABASE_BACKGROUND_WRITERS':'false','STRIPE_MODE':'test'}}
  hostname=lane['feature_tag']+'-sit.onwalk.net';account={'store':{'driver':'postgres','dsn':'postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=disable'},'server':{'publicUrl':'https://accounts-'+hostname,'allowedOrigins':['https://'+hostname,'https://accounts-'+hostname,'https://billing-'+hostname]},'auth':{'oauth':{'frontendUrl':'https://'+hostname,**{provider:{'redirectUrl':'https://accounts-'+hostname+'/api/auth/'+provider+'/callback'} for provider in ('github','google')}}},'agent':{'controllerUrl':'http://web-saas-accounts.'+ns+'.svc.cluster.local'}}
  next(x for x in docs if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-accounts-template')['data']['account.yaml']=yaml.safe_dump(account)
  next(x for x in docs if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values')['data']['values.yaml']=yaml.safe_dump(tagged_values)
  next(x for x in docs if x['kind']=='HelmRelease')['spec']['postRenderers']=[{'kustomize':{'patches':[{'target':{'kind':'ConfigMap','name':'web-saas-caddy-config'},'patch':yaml.safe_dump(caddy_patch)},{'target':{'kind':'NetworkPolicy','name':'web-saas-caddy'},'patch':yaml.safe_dump(ingress_patch)}]}}]
  ingress=next(x for x in docs if x['kind']=='Ingress')
  ingress['metadata']['annotations']={};ingress['spec']['ingressClassName']='caddy'
  for rule,host in zip(ingress['spec']['rules'],sorted(hosts)):rule['host']=host
  result=C.validate_documents(docs,'sit',candidate=candidate)
  self.assertEqual(result['namespace'],ns);self.assertEqual(set(result['hosts']),hosts)
  bad=copy.deepcopy(docs);next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-accounts-template')['data']['account.yaml']='store:\n  driver: postgres\n  dsn: postgres://admin:secret@production/db\n'
  with self.assertRaisesRegex(ValueError,'template database binding'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);cm=next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-accounts-template');modified=yaml.safe_load(cm['data']['account.yaml']);modified['server']['publicUrl']='https://accounts.svc.plus';cm['data']['account.yaml']=yaml.safe_dump(modified)
  with self.assertRaisesRegex(ValueError,'template escaped'):C.validate_documents(bad,'sit',candidate=candidate)
  for change in ({'accounts':{'enabled':False}},{'edgeGateway':{'enabled':True}},{'caddy':{'enabled':True,'httpOnly':True,'service':{'type':'NodePort'}}}):
   bad=copy.deepcopy(docs);cm=next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values');values=yaml.safe_load(cm['data']['values.yaml']);values.update(change);cm['data']['values.yaml']=yaml.safe_dump(values)
   with self.subTest(change=change),self.assertRaises(ValueError):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(candidate);bad['knowledge']['sha']='b'*40
  with self.assertRaisesRegex(ValueError,'knowledge source differs'):C.validate_documents(docs,'sit',candidate=bad)
  metrics_docs=copy.deepcopy(docs);cm=next(x for x in metrics_docs if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values');values=yaml.safe_load(cm['data']['values.yaml']);values['postgresql']['metrics']={'enabled':True};cm['data']['values.yaml']=yaml.safe_dump(values)
  with self.assertRaisesRegex(ValueError,'read-only database identity'):C.validate_documents(metrics_docs,'sit',candidate=candidate)
  metrics_patch=[{'op':'replace','path':'/spec/ingress','value':[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'datakit'}},'podSelector':{'matchLabels':{'app':'datakit'}}}],'ports':[{'protocol':'TCP','port':9187}]}]}]
  patches=next(x for x in metrics_docs if x['kind']=='HelmRelease')['spec']['postRenderers'][0]['kustomize']['patches'];patches.append({'target':{'kind':'NetworkPolicy','name':'web-saas-postgresql-exporter'},'patch':yaml.safe_dump(metrics_patch)})
  with self.assertRaisesRegex(ValueError,'read-only database identity'):C.validate_documents(metrics_docs,'sit',candidate=candidate)
  patches[-1]['patch']='- op: replace\n  path: /spec/ingress\n  value: [{}]'
  with self.assertRaisesRegex(ValueError,'declared agent'):C.validate_documents(metrics_docs,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);bad[0]['spec']['ingress']=[{}]
  with self.assertRaisesRegex(ValueError,'reviewed isolation'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);bad.append({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'allow-everything','namespace':ns},'spec':{'podSelector':{},'ingress':[{}]}})
  with self.assertRaisesRegex(ValueError,'reviewed isolation'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);bad[1]['spec']['egress'][0]['to'].append({'ipBlock':{'cidr':'0.0.0.0/0'}})
  with self.assertRaisesRegex(ValueError,'reviewed GitHub'):C.validate_documents(bad,'sit',candidate=candidate)
  for old,new in (('web-saas-console:80','external.example:80'),(lane['feature_tag']+'-sit.onwalk.net','other-sit.onwalk.net')):
   bad=copy.deepcopy(docs);patch=next(x for x in bad if x['kind']=='HelmRelease')['spec']['postRenderers'][0]['kustomize']['patches'][0]
   patch['patch']=patch['patch'].replace(old,new)
   with self.subTest(old=old),self.assertRaisesRegex(ValueError,'candidate routes'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);next(x for x in bad if x['kind']=='HelmRelease')['spec']['postRenderers']=[]
  with self.assertRaisesRegex(ValueError,'exactly one'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);patches=next(x for x in bad if x['kind']=='HelmRelease')['spec']['postRenderers'][0]['kustomize']['patches']
  patches.append({'target':{'kind':'Deployment','name':'web-saas-.*'},'patch':'{}'})
  with self.assertRaisesRegex(ValueError,'exact lane resource'):C.validate_documents(bad,'sit',candidate=candidate)
  patches[-1]={'target':{'kind':'Deployment','name':'web-saas-caddy'},'patch':'- op: replace\n  path: /spec/template/spec/containers\n  value: []'}
  with self.assertRaisesRegex(ValueError,'reviewed hardening'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);patches=next(x for x in bad if x['kind']=='HelmRelease')['spec']['postRenderers'][0]['kustomize']['patches'];patches[1]['patch']='- op: replace\n  path: /spec/ingress\n  value: [{}]'
  with self.assertRaisesRegex(ValueError,'restrict access'):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);cm=next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values');values=yaml.safe_load(cm['data']['values.yaml']);values['caddy']['httpOnly']=False;cm['data']['values.yaml']=yaml.safe_dump(values)
  with self.assertRaisesRegex(ValueError,'port 80'):C.validate_documents(bad,'sit',candidate=candidate)
  for component,env in (('accounts',{'DB_HOST':'production-db.svc.cluster.local'}),('accounts',{'POSTGRES_USER':'lane_admin'}),('accounts',{'STRIPE_MODE':'live'}),('accounts',{'DATABASE_BACKGROUND_WRITERS':'true'}),('console',{'DOCS_SERVICE_URL':'https://docs.svc.plus'})):
   bad=copy.deepcopy(docs);cm=next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values')
   values=yaml.safe_load(cm['data']['values.yaml']);values[component]['singleContainer' if component=='console' else 'workload']={'env':env};cm['data']['values.yaml']=yaml.safe_dump(values)
   with self.subTest(component=component),self.assertRaisesRegex(ValueError,'endpoint escaped'):C.validate_documents(bad,'sit',candidate=candidate)
  for component,env in (('content',{'KNOWLEDGE_REPO_REF':'main'}),('content',{'KNOWLEDGE_REPO_URL':'https://example.com/knowledge.git'}),('console',{'BRIDGE_SERVER_URL':'https://bridge.svc.plus'})):
   bad=copy.deepcopy(docs);cm=next(x for x in bad if x['kind']=='ConfigMap' and x['metadata']['name']=='web-saas-fullstack-values');values=yaml.safe_load(cm['data']['values.yaml']);values[component]['singleContainer' if component=='console' else 'workload']={'env':env};cm['data']['values.yaml']=yaml.safe_dump(values)
   with self.subTest(env=env),self.assertRaises(ValueError):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);bad[0]['metadata']['namespace']='production'
  with self.assertRaises(ValueError):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(candidate);bad['components']['accounts']['image']='ghcr.io/ai-workspace-services/accounts@sha256:'+'b'*64
  with self.assertRaisesRegex(ValueError,'differ from authenticated candidate'):C.validate_documents(docs,'sit',candidate=bad)
  bad=copy.deepcopy(candidate);bad['chart_digest']='sha256:'+'a'*64
  with self.assertRaisesRegex(ValueError,'chart source/digest'):C.validate_documents(docs,'sit',candidate=bad)
  bad=copy.deepcopy(docs);bad[-1]['spec']['rules'][0]['host']='other-sit.onwalk.net'
  with self.assertRaises(ValueError):C.validate_documents(bad,'sit',candidate=candidate)
  bad=copy.deepcopy(docs);bad[-1]['spec']['ingressClassName']='traefik'
  with self.assertRaisesRegex(ValueError,'reviewed Caddy ingress'):C.validate_documents(bad,'sit',candidate=candidate)
  with self.assertRaisesRegex(ValueError,'no separate TLS'):C.validate_documents(docs,'sit',True,candidate)
  with self.assertRaises(ValueError):C.validate_documents(docs,'uat',candidate=candidate)


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

 def test_tagged_verify_rejects_unbound_seed_before_apply(self):
  c=self.config();c.update(namespace='feature-sit-test-1483-sit',mode='verify',namespace_uid='3'*36,
      tagged_lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'e'*64})
  c['seed_receipt']={'schema_version':'dev-lane-seed/v1','result':'verified','namespace':c['namespace'],'namespace_uid':c['namespace_uid'],
     'candidate_sha':c['candidate_sha'],'node_uid':c['node_uid'],'cluster_uid':c['cluster_uid'],'instance_id':c['instance_id'],
     'database_roles':['xc_accounts_rw','xc_billing_rw'],'schema_initialized':True}
  base=self.baseline(c)
  class Client:
   def __init__(self):self.applied=[]
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace' and name=='kube-system':return base[1]
    if kind=='namespace':return {'metadata':{'uid':c['namespace_uid'],'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','environment':'sit','release.svc.plus/feature':'sit-test-1483','feature_id':'sit-test-1483','release.svc.plus/feature-tag':'smoke-sit-test-1483'}}}
    return base[2] if kind=='gitrepository' else base[3]
   def apply(self,docs):self.applied.append(docs)
  client=Client()
  with self.assertRaisesRegex(ValueError,'TLS and service health evidence'):R.execute(c,client)
  self.assertEqual(client.applied,[])

 def test_tagged_caddy_submit_requires_dns_before_any_apply(self):
  c=self.config();c.update(namespace='feature-sit-test-1483-sit',mode='submit',
      tagged_lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'e'*64})
  base=self.baseline(c)
  class Client:
   def __init__(self):self.applied=[]
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='nodes':return base[0]
    if kind=='namespace':return base[1] if name=='kube-system' else None
    return base[2] if kind=='gitrepository' else base[3]
   def apply(self,docs):self.applied.append(docs)
  client=Client()
  with self.assertRaisesRegex(ValueError,'public Dev ingress address'):R.execute(c,client)
  c['target_ip']='172.237.5.48'
  with self.assertRaisesRegex(ValueError,'DNS/HTTP evidence'):R.execute(c,client)
  self.assertEqual(client.applied,[])
  hosts=R.scope(c)[4]
  c['dns_http_proof']=[{'host':host,'addresses':[c['target_ip']],'http_status':308,
                         'redirect_location':'https://'+host+'/.well-known/acme-challenge/owner-preflight'} for host in hosts]
  c['dns_http_proof'][0]['redirect_location']='https://untrusted.invalid/'
  with self.assertRaisesRegex(ValueError,'DNS/HTTP evidence'):R.execute(c,client)
  self.assertEqual(client.applied,[])
  c['dns_http_proof'][0]['redirect_location']='https://'+c['dns_http_proof'][0]['host']+'/.well-known/acme-challenge/owner-preflight'
  self.assertEqual(R.execute(c,client)['result'],'submitted_pending_seed')
  self.assertTrue(client.applied)

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
 def test_referral_seed_owner_rejects_tagged_receipt_before_target_or_transport(self):
  from types import SimpleNamespace
  from unittest.mock import patch
  import run_dev_web_saas_seed as seed
  args=SimpleNamespace(phase='seed-admin',cmdb=None,cmdb_sha256='a'*64,instance_id='1',
                       candidate_receipt=None,candidate_sha256='b'*64,gitops_sha='c'*40,stage='sit')
  with patch.object(seed,'read_bound',return_value={}),patch.object(seed,'validate_cmdb',return_value={}), \
       patch.object(seed,'validate_candidate_receipt',return_value={'schema_version':'dev-feature-candidate/v2'}), \
       patch.object(seed.candidate,'validate_tree') as render,patch.object(seed,'binding') as transport:
   with self.assertRaisesRegex(ValueError,'database seed owner is not implemented'):seed.execute(args)
   render.assert_not_called();transport.assert_not_called()

 def test_tagged_receipt_requires_same_main_caller_run_attempt_and_fingerprint(self):
  from unittest.mock import patch
  import run_dev_feature_flux as owner
  repo='ai-workspace-infra/Platform-Orchestrator';sha='a'*40
  lane={'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'d'*64}
  projection={'tagged_lane':lane,'chart_digest':'sha256:'+'e'*64,'knowledge':{'repository':'ai-workspace-services/knowledge','sha':'a'*40},'components':{name:{} for name in ('caddy','postgresql','console','accounts','billing','content')}}
  source={'repository':repo,'workflow_ref':repo+'/.github/workflows/feature-lane-deploy.yml@refs/heads/main','run_id':'123','run_attempt':'2','workflow_sha':'f'*40}
  receipt={'schema_version':'dev-feature-candidate/v2','result':'verified','repository':'ai-workspace-infra/gitops','stage':'sit','candidate_head':sha,'candidate_sha256':'d'*64,'projection':projection,'source':source}
  env={'GITHUB_REPOSITORY':repo,'GITHUB_RUN_ATTEMPT':'2','GITHUB_SHA':'f'*40,'GITHUB_WORKFLOW_REF':source['workflow_ref']}
  with patch.dict('os.environ',env,clear=False):
   self.assertEqual(owner.validate_candidate_receipt(receipt,sha,'sit','123')['candidate_sha256'],'d'*64)
   for key,value in [('run_id','124'),('run_attempt','1'),('workflow_sha','b'*40),('workflow_ref',repo+'/.github/workflows/other.yml@refs/heads/main')]:
    bad=copy.deepcopy(receipt);bad['source'][key]=value
    with self.subTest(key=key),self.assertRaises(ValueError):owner.validate_candidate_receipt(bad,sha,'sit','123')
   bad=copy.deepcopy(receipt);bad['candidate_sha256']='c'*64
   with self.assertRaisesRegex(ValueError,'fingerprint mismatch'):owner.validate_candidate_receipt(bad,sha,'sit','123')
   with patch.dict('os.environ',{'GITHUB_WORKFLOW_REF':repo+'/.github/workflows/other.yml@refs/heads/main'}):
    with self.assertRaisesRegex(ValueError,'protected tagged caller'):owner.validate_candidate_receipt(receipt,sha,'sit','123')

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


class RebuildNamespaceTests(unittest.TestCase):
 def test_rebuild_calls_delete_without_preconditions_and_handles_text_output(self):
  import subprocess
  from unittest.mock import patch
  real_client=R.Client()
  with patch.object(subprocess,'run') as mock_run:
   mock_run.return_value=type('Process',(),{'returncode':0,'stdout':'namespace "feature-xconnect-referral-sit" deleted\n','stderr':''})()
   res=real_client.call(['delete','namespace','feature-xconnect-referral-sit','--wait=false'])
   self.assertEqual(res,'namespace "feature-xconnect-referral-sit" deleted')
  deleted_calls=[]
  class MockClient:
   def call(self,args,stdin=None):
    if args[:2]==['delete','namespace']:
     deleted_calls.append(args)
     return 'namespace "feature-xconnect-referral-sit" deleted'
    return None
   def get(self,kind,name=None,namespace=None,optional=False):
    if kind=='persistentvolumeclaims':return {'items':[]}
    if kind=='persistentvolumes':return {'items':[]}
    if kind=='namespace':
     if not deleted_calls:
      return {'metadata':{'uid':'3'*36,'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','release.svc.plus/feature':'xconnect-referral','feature_id':'xconnect-referral','environment':'sit'}}}
     if len(deleted_calls)==1:
      return None
     return {'metadata':{'uid':'5'*36,'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','release.svc.plus/feature':'xconnect-referral','feature_id':'xconnect-referral','environment':'sit'}}}
    return None
   def create(self,doc):
    deleted_calls.append(['created'])
    return {'metadata':{'uid':'5'*36}}
   def apply(self,docs):return None
  c=Contracts().config();c.update(mode='rebuild',namespace_uid='3'*36)
  existing={'metadata':{'uid':'3'*36,'labels':{'app.kubernetes.io/managed-by':'dev-feature-flux-owner','release.svc.plus/feature':'xconnect-referral','feature_id':'xconnect-referral','environment':'sit'}}}
  res=R.rebuild_namespace(c,MockClient(),existing,sleep=lambda s:None,clock=lambda:0)
  self.assertEqual(res['namespace_uid'],'5'*36)
  self.assertEqual(deleted_calls[0],['delete','namespace','feature-xconnect-referral-sit','--wait=false'])

if __name__=='__main__':unittest.main()
