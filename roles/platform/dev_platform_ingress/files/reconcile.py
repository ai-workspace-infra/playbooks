#!/usr/bin/env python3
"""Fixed Dev shared ingress only; no app candidate, root source or cluster RBAC."""
import datetime,hashlib,http.client,importlib.util,json,os,re,time
from pathlib import Path

def load_scope():
 path=Path(os.environ.get('DEV_PLATFORM_SCOPE_PATH',str(Path(__file__).resolve().parents[2]/'dev_feature_flux/files/reconcile.py')))
 spec=importlib.util.spec_from_file_location('shared_dev_foundation_scope',path)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
R=load_scope()
NS='platform-ingress';NAME='dev-platform-ingress';SA='platform-ingress-reconciler';DEPLOYMENT='dev-ingress-traefik'
PATH='./services/platform-ingress/dev'
MANIFEST='9c1ba9d73473f2067b2928eacbe3706207b09acae0830338013b20b1632117f4'
VALUES='7f664c6787d17bee1a07b4af395b8fcc4b2ac7cbfa341e0c59f5fc104dddb9e2'
CHART='sha256:49458ccd9673cd828ee0b091030f5747f5d95f205bbd0cf0b6c3bf757398cae1'
IMAGE='docker.io/library/traefik@sha256:1336e85aaed293bd63d005086e0dd013b04078b26aaeb1c18b26a2f928af7e65'
AMD64='sha256:abb2b9c1b132b5b0be40d09c85bf8d3dab2a59fa0eb3aa955ab8be5efbb73de6'
WATCH=['feature-xconnect-referral-sit','web-saas-uat']
OWNER='dev-platform-ingress-owner'


def require(value,message):
 if not value:raise ValueError(message)

def foundation(c):
 return {**c,'environment':'sit','namespace':WATCH[0],'mode':'plan'}

def validate(c):
 require(c.get('environment')=='dev' and c.get('namespace')==NS and c.get('mode') in ('plan','reconcile','verify'),'exact Dev platform operation required')
 R.validate(foundation(c));require(c['manifest_sha256']==MANIFEST,'platform content is not the reviewed immutable contract')

def preflight(c,client):
 nodes=client.get('nodes');cluster=client.get('namespace','kube-system');platform=client.get('gitrepository','platform-config','flux-system');root=client.get('kustomization','platform-root','flux-system')
 R.preflight(foundation(c),nodes,cluster,platform,root)
 require(nodes['items'][0].get('status',{}).get('nodeInfo',{}).get('architecture')=='amd64','only reviewed amd64 Dev node supported')
 for stage,(name,_,_) in R.SCOPES.items():
  obj=client.get('namespace',name,optional=True)
  if obj:
   desired=R.documents({**foundation(c),'environment':stage,'namespace':name})[0]['metadata']['labels']
   require(all(obj['metadata'].get('labels',{}).get(k)==v for k,v in desired.items()),'existing application namespace ownership/policy differs')
 obj=client.get('namespace',NS,optional=True)
 if obj:
  expected=next(d for d in documents(c) if d['kind']=='Namespace' and d['metadata']['name']==NS)['metadata']['labels']
  require(all(obj['metadata'].get('labels',{}).get(k)==v for k,v in expected.items()),'existing platform namespace ownership/policy differs; no takeover')
 pods=client.get('pods').get('items',[])
 for pod in pods:
  ports=[p.get('hostPort') for container in pod.get('spec',{}).get('containers',[]) for p in container.get('ports',[]) if p.get('hostPort') in (80,443)]
  if ports:require(pod['metadata'].get('namespace')==NS and pod['metadata'].get('labels',{}).get('app.kubernetes.io/name')=='traefik','hostPorts already owned by another workload')


def watch_rules():
 # Exactly the namespaced watcher permissions rendered by official chart42.1.3.
 return [{'apiGroups':[''],'resources':['configmaps','services','secrets'],'verbs':['get','list','watch']},
         {'apiGroups':['discovery.k8s.io'],'resources':['endpointslices'],'verbs':['list','watch']},
         {'apiGroups':[''],'resources':['pods'],'verbs':['get']},
         {'apiGroups':['extensions','networking.k8s.io'],'resources':['ingresses'],'verbs':['get','list','watch']},
         {'apiGroups':['extensions','networking.k8s.io'],'resources':['ingresses/status'],'verbs':['update']}]

def documents(c):
 lanes=[R.namespace_documents({**foundation(c),'environment':stage,'namespace':name}) for stage,(name,_,_) in R.SCOPES.items()]
 docs=[lane[0] for lane in lanes]
 docs.append({'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS,'labels':{'app.kubernetes.io/managed-by':OWNER,'environment':'dev','app.kubernetes.io/part-of':'dev-platform-ingress','pod-security.kubernetes.io/enforce':'privileged','pod-security.kubernetes.io/audit':'restricted','pod-security.kubernetes.io/warn':'restricted'},'annotations':{'security.svc.plus/exception':'Only reviewed hostPorts80/443; non-root/dropALL/no privileged/no hostNetwork; persistent platform scope.'}}})
 docs.extend(doc for lane in lanes for doc in lane[1:])
 def meta(name,ns=NS):return {'name':name,'namespace':ns,'labels':{'app.kubernetes.io/managed-by':OWNER}}
 docs.append({'apiVersion':'v1','kind':'ServiceAccount','metadata':meta(SA),'automountServiceAccountToken':False})
 for ns in [NS,*WATCH]:
  rules=watch_rules()+[{'apiGroups':['rbac.authorization.k8s.io'],'resources':['roles','rolebindings'],'verbs':['get','list','watch','create','update','patch','delete']}]
  if ns==NS:
   rules += [{'apiGroups':[''],'resources':['serviceaccounts','configmaps','secrets'],'verbs':['get','list','watch','create','update','patch','delete']},
             {'apiGroups':[''],'resources':['persistentvolumeclaims'],'verbs':['get','list','watch','create','update','patch']},
             {'apiGroups':[''],'resources':['pods'],'verbs':['get','list','watch']},
             {'apiGroups':['apps'],'resources':['deployments','replicasets'],'verbs':['get','list','watch','create','update','patch','delete']},
             {'apiGroups':['source.toolkit.fluxcd.io'],'resources':['ocirepositories'],'verbs':['get','list','watch','create','update','patch','delete']},
             {'apiGroups':['helm.toolkit.fluxcd.io'],'resources':['helmreleases'],'verbs':['get','list','watch','create','update','patch','delete']}]
  docs.append({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':meta(SA,ns),'rules':rules})
  docs.append({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':meta(SA,ns),'subjects':[{'kind':'ServiceAccount','name':SA,'namespace':NS}],'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':SA}})
 docs.append({'apiVersion':'source.toolkit.fluxcd.io/v1','kind':'GitRepository','metadata':meta(NAME),'spec':{'interval':'1m','url':R.REPO,'ref':{'commit':c['candidate_sha']}}})
 docs.append({'apiVersion':'kustomize.toolkit.fluxcd.io/v1','kind':'Kustomization','metadata':meta(NAME),'spec':{'interval':'1m','path':PATH,'prune':False,'wait':True,'timeout':'15m','serviceAccountName':SA,'targetNamespace':NS,'sourceRef':{'kind':'GitRepository','name':NAME},'patches':[{'target':{'kind':'Namespace','name':NS},'patch':'apiVersion: v1\nkind: Namespace\nmetadata:\n  name: platform-ingress\n$patch: delete\n'}]}})
 return docs


def verify_pod(pod):
 spec=pod.get('spec',{});require(spec.get('nodeName')==R.NODE and not spec.get('hostNetwork') and not spec.get('hostPID') and not spec.get('hostIPC'),'unexpected node/host namespace')
 require(not spec.get('initContainers') and not spec.get('ephemeralContainers') and all('hostPath' not in v for v in spec.get('volumes',[])),'unreviewed init/host volume')
 security=spec.get('securityContext',{});require(security.get('runAsNonRoot') is True and security.get('runAsUser')==65532 and security.get('runAsGroup')==65532 and security.get('fsGroup')==65532 and security.get('seccompProfile')=={'type':'RuntimeDefault'},'non-root runtime identity differs')
 require(spec.get('serviceAccountName')==DEPLOYMENT,'fixed namespaced Traefik identity required')
 containers=spec.get('containers',[]);require(len(containers)==1 and containers[0].get('image')==IMAGE,'fixed single Traefik image required')
 container=containers[0];context=container.get('securityContext',{})
 require(not context.get('privileged') and context.get('allowPrivilegeEscalation') is False and context.get('capabilities',{}).get('drop')==['ALL'] and not context.get('capabilities',{}).get('add') and context.get('readOnlyRootFilesystem') is True,'container privileges differ')
 ports={(x.get('hostPort'),x.get('containerPort'),x.get('protocol','TCP')) for x in container.get('ports',[]) if x.get('hostPort')}
 require(ports=={(80,8000,'TCP'),(443,8443,'TCP')},'only reviewed hostPorts80/443 allowed')
 args=container.get('args',[])
 require('--providers.kubernetesingress.ingressClass=dev-web-saas' in args and '--providers.kubernetesingress.disableClusterScopeResources=true' in args and '--providers.kubernetesingress.namespaces='+','.join(WATCH) in args,'HTTP class or namespace watch boundary differs')
 providers=[arg for arg in args if arg.startswith('--providers.')]
 allowed={'--providers.kubernetesingress','--providers.kubernetesingress.allowEmptyServices=false','--providers.kubernetesingress.ingressClass=dev-web-saas','--providers.kubernetesingress.disableClusterScopeResources=true','--providers.kubernetesingress.namespaces='+','.join(WATCH)}
 require(len(providers)==len(allowed) and set(providers)==allowed,'unreviewed/duplicate provider namespace/class flags')
 status=pod.get('status',{});rows=status.get('containerStatuses',[])
 require(status.get('phase')=='Running' and any(x.get('type')=='Ready' and x.get('status')=='True' for x in status.get('conditions',[])) and len(rows)==1 and rows[0].get('ready') is True,'Traefik Pod not Ready')
 digest=rows[0].get('imageID','').split('@')[-1].removeprefix('containerd://')
 require(digest in (IMAGE.split('@')[1],AMD64),'runtime imageID is not reviewed index/amd64 manifest')
 return {'pod_uid':pod['metadata']['uid'],'node_name':spec['nodeName'],'image':IMAGE,'image_id':rows[0]['imageID'],'host_ports':[80,443],'ingress_class':'dev-web-saas','watch_namespaces':WATCH}


class Client(R.Client):
 def get(self,kind,name=None,namespace=None,optional=False):
  if kind=='pods' and namespace is None:return self.call(['get','pods','-A','-o','json'])
  return super().get(kind,name,namespace,optional)

def http_listener():
 connection=http.client.HTTPConnection('127.0.0.1',80,timeout=5)
 try:
  connection.request('GET','/',headers={'Host':'dev-platform-preflight.invalid'})
  return connection.getresponse().status
 finally:connection.close()

def execute(c,client=None,sleep=time.sleep,clock=time.monotonic,http_probe=http_listener):
 validate(c);client=client or Client();preflight(c,client)
 report={'schema_version':'dev-platform-ingress/v1','result':'planned','changed':False,'namespace':NS,'candidate_sha':c['candidate_sha'],'manifest_sha256':MANIFEST,'owner_sha':c['owner_sha'],'platform_sha':c['platform_sha'],'node_uid':c['node_uid'],'cluster_uid':c['cluster_uid'],'business_acceptance':'NOT_RUN','tls_certificate_acceptance':'NOT_RUN'}
 if c['mode']=='plan':return report
 if c['mode']=='reconcile':
  try:client.apply(documents(c));report['changed']=True
  except Exception:report.update(result='apply_failed_or_unknown',changed='UNKNOWN');return report
 deadline=clock()+int(c.get('wait_seconds',900))
 while True:
  source=client.get('gitrepository',NAME,NS,optional=True);root=client.get('kustomization',NAME,NS,optional=True);hr=client.get('helmrelease','dev-ingress',NS,optional=True)
  if source and root and hr and R.ready(source) and R.ready(root) and R.ready(hr):
   require(source['spec'].get('url')==R.REPO and source['spec']['ref']=={'commit':c['candidate_sha']} and R.revision(source['status']['artifact']['revision'],c['candidate_sha']) and R.revision(root['status']['lastAppliedRevision'],c['candidate_sha']),'ingress fixed-source drift')
   wanted=documents(c)[-1]['spec'];actual=root['spec']
   require(all(actual.get(k)==v for k,v in wanted.items()) and set(actual)-set(wanted)<={'force','suspend'} and not actual.get('force') and not actual.get('suspend'),'platform reconciler scope drift')
   require(hr['spec']['serviceAccountName']==SA and hr['spec']['chartRef']=={'kind':'OCIRepository','name':'traefik-chart'} and hr['spec'].get('install',{}).get('crds')=='Skip' and hr['spec'].get('upgrade',{}).get('crds')=='Skip','Helm identity/chart scope drift')
   require(hr['spec'].get('releaseName')=='dev-ingress' and hr['spec'].get('valuesFrom')==[{'kind':'ConfigMap','name':'dev-ingress-values','valuesKey':'values.yaml'}] and not hr['spec'].get('values') and not hr['spec'].get('postRenderers'),'Helm values source/post-render drift')
   for expected in documents(c):
    if expected['kind'] in ('Role','RoleBinding'):
     live=client.get(expected['kind'].lower(),expected['metadata']['name'],expected['metadata']['namespace'])
     keys=['rules'] if expected['kind']=='Role' else ['subjects','roleRef']
     require(all(live.get(key)==expected[key] for key in keys),'controller RBAC scope drift')
   chart=client.get('ocirepository','traefik-chart',NS);require(chart['spec']['url']=='oci://ghcr.io/traefik/helm/traefik' and chart['spec']['ref']=={'digest':CHART},'chart digest/source drift')
   values=client.get('configmap','dev-ingress-values',NS)
   require(hashlib.sha256(values['data']['values.yaml'].encode()).hexdigest()==VALUES,'live platform values differ')
   pods=client.get('pods',namespace=NS).get('items',[])
   active=[p for p in pods if not p['metadata'].get('deletionTimestamp')]
   require(len(active)==1,'single platform Traefik Pod required')
   report.update(verify_pod(active[0]));preflight(c,client)
   status=http_probe();require(status==404,'unmatched-host HTTP listener must be reachable without routing to an application')
   report['http_listener_status']=status
   report.update(result='verified',helm_ready=True,source_revision=source['status']['artifact']['revision'],applied_revision=root['status']['lastAppliedRevision'],observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());return report
  if clock()>=deadline:report['result']='not_ready';return report
  sleep(5)

if __name__=='__main__':
 try:
  result=execute(json.loads(os.environ['DEV_PLATFORM_CONFIG']));print(json.dumps(result,sort_keys=True));raise SystemExit(0 if result['result'] in ('planned','verified') else 1)
 except Exception as e:
  print(json.dumps({'schema_version':'dev-platform-ingress/v1','result':'blocked_or_failed','error_type':type(e).__name__}));raise SystemExit(2)
