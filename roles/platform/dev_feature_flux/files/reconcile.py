#!/usr/bin/env python3
"""Trusted Dev SIT namespace owner; never promotes the platform root to a PR."""
import datetime,ipaddress,json,os,re,subprocess,time

NS='feature-xconnect-referral-sit'
FEATURE='xconnect-referral'
NODE='dev-k3s-v5'
SOURCE='xconnect-referral-sit'
PATH='./services/distributed-web-saas-fullstack/dev/sit-xconnect-referral'
REPO='https://github.com/ai-workspace-infra/gitops.git'
CHART='sha256:6336235445e389958660cc344eead488ad3a50cf67afb1301fd35f6793720f27'
SCOPES={
 'sit':('feature-xconnect-referral-sit','xconnect-referral-sit','./services/distributed-web-saas-fullstack/dev/sit-xconnect-referral'),
 'uat':('web-saas-uat','web-saas-uat','./services/distributed-web-saas-fullstack/dev/uat')}
DOMAINS={
 'sit':{'xconnect-referral-sit.onwalk.net','accounts-xconnect-referral-sit.onwalk.net','billing-xconnect-referral-sit.onwalk.net'},
 'uat':{'web-saas-uat.onwalk.net','accounts-web-saas-uat.onwalk.net','billing-web-saas-uat.onwalk.net'}}
SHA=re.compile(r'^[0-9a-f]{40}$')
UUID=re.compile(r'^[0-9a-f-]{36}$')

# Only stable, reviewed categories cross the owner boundary. Never put exception
# text, Kubernetes stderr, request data, or secret values in a receipt.
FAILURE_CODE_BY_REASON={
 ('rebuild','SIT namespace incarnation changed before rebuild'):'sit_rebuild_namespace_uid_mismatch',
 ('rebuild','namespace ownership mismatch; refuse rebuild'):'sit_rebuild_namespace_ownership_mismatch',
 ('rebuild','persistent volume claim ownership mismatch'):'sit_rebuild_pv_claim_mismatch',
 ('rebuild','SIT rebuild cannot safely reclaim this persistent volume'):'sit_rebuild_pv_policy_rejected',
 ('rebuild','in-progress SIT deletion references a volume that cannot be safely reclaimed'):'sit_rebuild_pv_policy_rejected',
 ('rebuild','unmatched persistent volume references the SIT namespace'):'sit_rebuild_orphaned_pv',
 ('rebuild','orphaned persistent volume still references the deleted SIT namespace'):'sit_rebuild_orphaned_pv',
 ('rebuild','namespace deletion timed out; no force-finalizer removal'):'sit_rebuild_namespace_delete_timeout',
 ('rebuild','SIT local-path volume reclamation timed out; namespace remains deleted and rebuild is blocked'):'sit_rebuild_pv_reclaim_timeout',
 ('rebuild','namespace name was recreated by another actor during deletion'):'sit_rebuild_namespace_recreated_concurrently',
 ('rebuild','namespace name was claimed during rebuild; refusing adoption'):'sit_rebuild_namespace_claimed',
 ('rebuild','recreated SIT namespace UID did not change'):'sit_rebuild_namespace_uid_invalid',
 ('rebuild','recreated SIT namespace labels mismatch'):'sit_rebuild_namespace_owner_mismatch',
 ('rebuild','selected Kubernetes operation failed; raw output suppressed'):'sit_rebuild_kubernetes_operation_failed',
 ('seed-admin','namespace UID changed or stale target'):'sit_seed_namespace_uid_mismatch',
 ('seed-admin','namespace is terminating'):'sit_seed_namespace_terminating',
 ('seed-admin','namespace stage mismatch'):'sit_seed_namespace_stage_mismatch',
 ('seed-admin','cluster identity mismatch'):'sit_seed_cluster_identity_mismatch',
 ('seed-admin','single-node identity mismatch'):'sit_seed_node_identity_mismatch',
 ('seed-admin','Dev seed requires trusted CMDB-bound caller request'):'sit_seed_caller_contract_invalid',
 ('seed-admin','Kubernetes operation failed (sensitive output suppressed)'):'sit_seed_kubernetes_operation_failed',
 ('initialize','namespace UID changed or stale target'):'sit_seed_namespace_uid_mismatch',
 ('initialize','namespace is terminating'):'sit_seed_namespace_terminating',
 ('initialize','namespace stage mismatch'):'sit_seed_namespace_stage_mismatch',
 ('initialize','cluster identity mismatch'):'sit_seed_cluster_identity_mismatch',
 ('initialize','single-node identity mismatch'):'sit_seed_node_identity_mismatch',
 ('initialize','Dev seed requires trusted CMDB-bound caller request'):'sit_seed_caller_contract_invalid',
 ('initialize','Kubernetes operation failed (sensitive output suppressed)'):'sit_seed_kubernetes_operation_failed',
}

# Fixed, non-sensitive platform preflight reasons apply to every lane mode.
PREFLIGHT_FAILURE_CODE_BY_REASON={
 'actual Dev node identity mismatch':'dev_platform_node_identity_mismatch',
 'actual cluster identity mismatch':'dev_platform_cluster_identity_mismatch',
 'Dev node not Ready':'dev_platform_node_not_ready',
 'protected platform source mismatch':'dev_platform_source_mismatch_or_not_ready',
 'protected source revision mismatch':'dev_platform_source_revision_mismatch',
 'protected platform root not Ready':'dev_platform_root_not_ready_or_revision_mismatch',
 'Dev ingress source not Ready or identity mismatch':'dev_ingress_source_not_ready_or_identity_mismatch',
 'Dev ingress source revision invalid':'dev_ingress_source_revision_invalid',
 'Dev ingress not Ready or revision mismatch':'dev_ingress_not_ready_or_revision_mismatch',
}

def failure_code(operation,error):
 if isinstance(error,subprocess.TimeoutExpired):return 'owner_timeout'
 if isinstance(error,KeyError):return 'owner_input_missing'
 if isinstance(error,(ValueError,RuntimeError)) or type(error).__name__=='Blocked':
  reason=error.args[0] if error.args and isinstance(error.args[0],str) else None
  return FAILURE_CODE_BY_REASON.get((operation,reason), PREFLIGHT_FAILURE_CODE_BY_REASON.get(reason, 'owner_rejected_or_internal_error'))
 return 'owner_internal_error'

def require(value,message):
 if not value: raise ValueError(message)

def verify_seed_baseline(seed, config):
 digest=config.get('data_baseline_sha256')
 require(isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest) and
         seed.get('data_baseline_sha256')==digest,
         'seed data baseline differs from verified candidate')

def scope(c):
 """Derive the only allowed target from a caller-authenticated tagged lane."""
 lane=c.get('tagged_lane')
 if lane is None:
  require(c.get('environment') in SCOPES,'fixed Dev lane scope required')
  ns,source,path=SCOPES[c['environment']]
  return ns,source,path,FEATURE,DOMAINS[c['environment']],None
 require(c.get('environment')=='sit' and isinstance(lane,dict) and set(lane)=={'feature_id','feature_tag','candidate_sha256'},'tagged lane requires the SIT candidate binding')
 feature=lane.get('feature_id');tag=lane.get('feature_tag');fingerprint=lane.get('candidate_sha256')
 require(isinstance(feature,str) and re.fullmatch(r'[a-z][a-z0-9-]{1,45}',feature) and not feature.endswith('-'),'invalid tagged Feature ID')
 suffix='-'+feature
 require(isinstance(tag,str) and tag.endswith(suffix) and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',tag[:-len(suffix)]),'tag does not bind Feature ID')
 require(isinstance(fingerprint,str) and re.fullmatch(r'[0-9a-f]{64}',fingerprint),'candidate fingerprint required')
 label=tag+'-sit'
 require(all(len(prefix+label)<=63 for prefix in ('','accounts-','billing-')),'tagged lane hostname exceeds DNS label limit')
 ns='feature-'+feature+'-sit'
 domains={prefix+label+'.onwalk.net' for prefix in ('','accounts-','billing-')}
 return ns,ns,'./services/distributed-web-saas-fullstack/dev/feature-lanes/'+feature,feature,domains,tag

def validate(c):
 ns,_,_,_,_,tag=scope(c)
 require(c.get('namespace')==ns and c.get('node_name')==NODE,'fixed Dev lane scope required')
 require(c.get('mode') in ('plan','prepare','rebuild','submit','verify','enable-tls'),'explicit mode required')
 if tag is not None:require(c.get('mode')!='enable-tls','tagged Caddy lane has no separate TLS ingress phase')
 if c.get('mode')=='rebuild':
  require(tag is None,'tagged lane rebuild is not authorized by this owner')
  require(c.get('environment')=='sit' and UUID.fullmatch(c.get('namespace_uid','')),'SIT rebuild requires the authenticated current namespace UID')
 for key in ('candidate_sha','platform_sha','owner_sha'):
  require(SHA.fullmatch(c.get(key,'')), 'full immutable '+key+' required')
 for key in ('node_uid','cluster_uid'):
  require(UUID.fullmatch(c.get(key,'')),'exact '+key+' required')
 require(re.fullmatch(r'[0-9a-f]{64}',c.get('manifest_sha256','')),'validated candidate manifest digest required')
 require(10<=int(c.get('wait_seconds',900))<=1800,'bounded wait required')

def ready(obj):
 return (obj.get('status',{}).get('observedGeneration')==obj.get('metadata',{}).get('generation') and
         any(x.get('type')=='Ready' and x.get('status')=='True' for x in obj.get('status',{}).get('conditions',[])))

def revision(value,sha):
 return value in ('sha1:'+sha,'main@sha1:'+sha) or (isinstance(value,str) and value.endswith('@sha1:'+sha))

def preflight(c,nodes,cluster,platform,root):
 validate(c)
 items=nodes.get('items',[])
 require(len(items)==1 and items[0]['metadata']['name']==NODE and items[0]['metadata']['uid']==c['node_uid'],'actual Dev node identity mismatch')
 require(cluster['metadata']['uid']==c['cluster_uid'],'actual cluster identity mismatch')
 require(any(x.get('type')=='Ready' and x.get('status')=='True' for x in items[0].get('status',{}).get('conditions',[])),'Dev node not Ready')
 if c.get('tagged_lane') is not None:
  require(platform.get('metadata',{}).get('name')=='platform-config-dev' and platform.get('metadata',{}).get('namespace')=='flux-system' and platform.get('spec',{}).get('url')==REPO and platform.get('spec',{}).get('ref')=={'branch':'main'} and ready(platform),'Dev ingress source not Ready or identity mismatch')
  rev=platform.get('status',{}).get('artifact',{}).get('revision','')
  require(isinstance(rev,str) and re.fullmatch(r'main@sha1:[0-9a-f]{40}',rev),'Dev ingress source revision invalid')
  require(root.get('metadata',{}).get('name')=='platform-ingress-dev' and root.get('metadata',{}).get('namespace')=='flux-system' and root.get('spec',{}).get('path')=='./services/platform-ingress/dev' and root.get('spec',{}).get('sourceRef')=={'kind':'GitRepository','name':'platform-config-dev'} and ready(root) and root.get('status',{}).get('lastAppliedRevision')==rev,'Dev ingress not Ready or revision mismatch')
  return
 require(platform.get('spec',{}).get('ref')=={'commit':c['platform_sha']} and ready(platform),'protected platform source mismatch')
 require(revision(platform.get('status',{}).get('artifact',{}).get('revision'),c['platform_sha']),'protected source revision mismatch')
 require(ready(root) and revision(root.get('status',{}).get('lastAppliedRevision'),c['platform_sha']),'protected platform root not Ready')

def documents(c):
 NS,SOURCE,PATH,feature,_,tag=scope(c)
 labels={'app.kubernetes.io/managed-by':'dev-feature-flux-owner','environment':c['environment'],'release.svc.plus/feature':feature,'feature_id':feature}
 if tag is not None:labels['release.svc.plus/feature-tag']=tag
 meta={'namespace':NS,'labels':labels}
 namespace={'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS,'labels':{**meta['labels'],'pod-security.kubernetes.io/enforce':'restricted','pod-security.kubernetes.io/enforce-version':'v1.37'}}}
 sa={'apiVersion':'v1','kind':'ServiceAccount','metadata':{**meta,'name':'lane-reconciler'},'automountServiceAccountToken':False}
 rules=[{'apiGroups':[''],'resources':['configmaps','secrets','services'],'verbs':['get','list','watch','create','update','patch','delete']},
        {'apiGroups':[''],'resources':['persistentvolumeclaims'],'verbs':['get','list','watch','create','update','patch']},
        {'apiGroups':['apps'],'resources':['deployments','statefulsets','replicasets'],'verbs':['get','list','watch','create','update','patch','delete']},
        {'apiGroups':['networking.k8s.io'],'resources':['ingresses','networkpolicies'],'verbs':['get','list','watch','create','update','patch','delete']},
        {'apiGroups':['helm.toolkit.fluxcd.io'],'resources':['helmreleases'],'verbs':['get','list','watch','create','update','patch','delete']},
        {'apiGroups':['source.toolkit.fluxcd.io'],'resources':['ocirepositories'],'verbs':['get','list','watch','create','update','patch','delete']}]
 role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{**meta,'name':'lane-reconciler'},'rules':rules}
 binding={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{**meta,'name':'lane-reconciler'},'subjects':[{'kind':'ServiceAccount','name':'lane-reconciler','namespace':NS}],'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'lane-reconciler'}}
 auditor={'apiVersion':'v1','kind':'ServiceAccount','metadata':{**meta,'name':'lane-auditor'},'automountServiceAccountToken':False}
 audit_role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{**meta,'name':'lane-auditor'},'rules':[{'apiGroups':[''],'resources':['pods','services'],'verbs':['get','list','watch']},{'apiGroups':['apps'],'resources':['deployments','statefulsets','replicasets'],'verbs':['get','list','watch']}]}
 audit_binding={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{**meta,'name':'lane-auditor'},'subjects':[{'kind':'ServiceAccount','name':'lane-auditor','namespace':NS}],'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'lane-auditor'}}
 source={'apiVersion':'source.toolkit.fluxcd.io/v1','kind':'GitRepository','metadata':{**meta,'name':SOURCE},'spec':{'interval':'1m','url':REPO,'ref':({'branch':'main'} if c['environment']=='uat' else {'commit':c['candidate_sha']})}}
 kustom={'apiVersion':'kustomize.toolkit.fluxcd.io/v1','kind':'Kustomization','metadata':{**meta,'name':SOURCE},'spec':{'interval':'1m','path':PATH,'prune':False,'wait':True,'timeout':'15m','serviceAccountName':'lane-reconciler','targetNamespace':NS,'sourceRef':{'kind':'GitRepository','name':SOURCE}}}
 if c['mode']=='enable-tls':
  kustom['metadata']['name']=SOURCE+'-tls';kustom['spec']['path']=PATH+'/tls'
  return [kustom]
 return [namespace,sa,role,binding,auditor,audit_role,audit_binding,source,kustom]

def namespace_documents(c):
 """Trusted namespace identities shared by feature and independent ingress owner."""
 return documents(dict(c,mode='prepare'))[:7]

def verify_tagged_readiness_probe(component,container):
 if component in ('billing','content'):
  probe=container.get('readinessProbe',{}).get('httpGet',{})
  require(set(probe)<={'path','port','scheme'} and probe.get('path')=='/readyz' and probe.get('port')==8080 and probe.get('scheme','HTTP')=='HTTP','tagged service readiness probe differs from reviewed contract')

def verify_runtime(c,pods):
 mapping=c.get('runtime_mapping',{});records=mapping.get('images',{})
 names={'accounts','billing','caddy','console','content','postgresql'}
 require(mapping.get('schema_version')=='dev-oci-platform/v1' and set(records)==names,'authenticated six-component OCI mapping required')
 binding=mapping.get('binding',{})
 for key in ('candidate_sha','namespace','namespace_uid','node_uid','cluster_uid','manifest_sha256'):require(binding.get(key)==c[key],'runtime mapping target/candidate mismatch')
 selected=[]
 for pod in pods.get('items',[]):
  meta=pod.get('metadata',{});labels=meta.get('labels',{})
  if meta.get('deletionTimestamp'):continue
  if labels.get('app.kubernetes.io/instance')!='web-saas':continue
  require(labels.get('app.kubernetes.io/component') in names,'unexpected application component pod')
  selected.append(pod)
 require(len(selected)==6,'exactly six active component pods required')
 result={}
 for pod in selected:
  meta=pod['metadata'];component=meta['labels']['app.kubernetes.io/component'];expected=records[component]
  require(component not in result and meta.get('namespace')==c['namespace'],'duplicate or wrong namespace component')
  spec=pod.get('spec',{});containers=spec.get('containers',[])
  require(spec.get('nodeName')==NODE and not spec.get('initContainers') and not spec.get('ephemeralContainers') and len(containers)==1 and containers[0].get('name')==component and containers[0].get('image')==expected['image'],'runtime container spec differs from candidate')
  if c.get('tagged_lane') is not None:verify_tagged_readiness_probe(component,containers[0])
  annotations=meta.get('annotations',{})
  require(annotations.get('release.svc.plus/source_repository')==expected['source_repository'] and annotations.get('release.svc.plus/source_sha')==expected['source_sha'] and annotations.get('release.svc.plus/image_digest')==expected['manifest_digest'] and str(annotations.get('release.svc.plus/build_run_id'))==str(expected['build_run_id']),'runtime release annotations mismatch')
  status=pod.get('status',{});states=status.get('containerStatuses',[])
  require(status.get('phase')=='Running' and any(x.get('type')=='Ready' and x.get('status')=='True' for x in status.get('conditions',[])) and len(states)==1 and states[0].get('name')==component and states[0].get('ready') is True and states[0].get('state',{}).get('running') is not None,'component not actually Running/Ready')
  image_id=states[0].get('imageID','');value=image_id
  for prefix in ('docker-pullable://','containerd://','docker://'):
   if value.startswith(prefix):value=value[len(prefix):];break
  if '@' in value:
   repository,value=value.split('@',1);require(repository==expected['image'].split('@')[0],'runtime image repository mismatch')
  require(value in (expected['manifest_digest'],expected['platform_manifest_digest']) and re.fullmatch(r'sha256:[0-9a-f]{64}',value),'runtime imageID is neither approved root nor verified amd64 child')
  result[component]={'pod':meta['name'],'pod_uid':meta['uid'],'spec_image':containers[0]['image'],'image_id':image_id,'root_digest':expected['manifest_digest'],'platform_digest':expected['platform_manifest_digest'],'ready':True}
 return result

def verify_sit_mail_sink(c,pods,client):
 """Bind the non-forwarding test mail Pod to its reviewed image and node."""
 image='ghcr.io/axllent/mailpit@sha256:0790692be935ea4420d085694f7985b1f692ca6a8771ab0e0bd8ba75ac180498'
 root='sha256:0790692be935ea4420d085694f7985b1f692ca6a8771ab0e0bd8ba75ac180498'
 amd64='sha256:938ce54b7b946ef3f45a03e119952cf7e67a7969d61f45a3ed055442a77e00f4'
 selected=[p for p in pods.get('items',[]) if not p.get('metadata',{}).get('deletionTimestamp') and p.get('metadata',{}).get('labels',{}).get('websaas.io/workload')=='sit-mail-sink']
 require(len(selected)==1,'exactly one isolated SIT mail sink required')
 pod=selected[0];meta=pod.get('metadata',{});spec=pod.get('spec',{});containers=spec.get('containers',[]);status=pod.get('status',{});states=status.get('containerStatuses',[])
 require(meta.get('namespace')==c['namespace'] and spec.get('nodeName')==NODE and not spec.get('hostNetwork') and
         spec.get('automountServiceAccountToken') is False and len(containers)==1 and
         containers[0].get('name')=='mail-sink' and containers[0].get('image')==image and
         not spec.get('initContainers') and not spec.get('ephemeralContainers'), 'SIT mail sink runtime scope differs')
 require(status.get('phase')=='Running' and any(x.get('type')=='Ready' and x.get('status')=='True' for x in status.get('conditions',[])) and
         len(states)==1 and states[0].get('name')=='mail-sink' and states[0].get('ready') is True and
         states[0].get('state',{}).get('running') is not None,'SIT mail sink is not Ready')
 image_id=states[0].get('imageID','');value=image_id
 for prefix in ('docker-pullable://','containerd://','docker://'):
  if value.startswith(prefix):value=value[len(prefix):];break
 if '@' in value:
  repository,value=value.split('@',1);require(repository=='ghcr.io/axllent/mailpit','SIT mail sink repository differs')
 require(value in (root,amd64),'SIT mail sink runtime image differs')
 name=meta.get('name');uid=meta.get('uid')
 require(isinstance(name,str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]*',name) and UUID.fullmatch(uid or ''),'SIT mail sink Pod identity invalid')
 current=client.get('pod',name,c['namespace'])
 require(current.get('metadata',{}).get('uid')==uid and not current.get('metadata',{}).get('deletionTimestamp'),'SIT mail sink Pod changed before SMTP probe')
 policies=client.get('networkpolicies',namespace=c['namespace']).get('items',[])
 def matches(selector,labels):
  for k,v in selector.get('matchLabels',{}).items():
   if labels.get(k)!=v:return False
  for expr in selector.get('matchExpressions',[]):
   key=expr.get('key');op=expr.get('operator');values=expr.get('values',[])
   if op=='In' and (key not in labels or labels[key] not in values):return False
   elif op=='NotIn' and key in labels and labels[key] in values:return False
   elif op=='Exists' and key not in labels:return False
   elif op=='DoesNotExist' and key in labels:return False
   elif op not in ('In','NotIn','Exists','DoesNotExist'):raise ValueError('unsupported network policy selector')
  return True
 selected_policies=[p for p in policies if matches(p.get('spec',{}).get('podSelector',{}),meta.get('labels',{}))]
 require(len(selected_policies)==1 and selected_policies[0].get('metadata',{}).get('name')=='sit-mail-sink-isolation','SIT mail sink selected by unexpected network policy')
 policy=selected_policies[0].get('spec',{})
 require(policy.get('podSelector')=={'matchLabels':{'websaas.io/workload':'sit-mail-sink'}} and
         set(policy.get('policyTypes',[]))=={'Ingress','Egress'} and not policy.get('egress') and
         policy.get('ingress')==[{'from':[{'podSelector':{'matchLabels':{'websaas.io/workload':'accounts'}}}],'ports':[{'protocol':'TCP','port':1025}]}],
         'SIT mail sink live isolation policy differs')
 message='From: sit-test-1483@invalid.example\nTo: test@invalid.example\nSubject: SIT isolation probe\n\nIsolated test.\n'
 client.call(['exec','-i','-n',c['namespace'],name,'-c','mail-sink','--','/mailpit','sendmail','-S','127.0.0.1:1025','test@invalid.example'],message)
 return {'pod':name,'pod_uid':uid,'image':image,'image_id':image_id,'ready':True,'egress_policy':'deny_all_live','smtp_test_accepted':True}


class Client:
 def call(self,args,stdin=None):
  p=subprocess.run(['/usr/local/bin/k3s','kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=20s',*args],input=stdin,text=True,capture_output=True,timeout=30)
  require(p.returncode==0,'selected Kubernetes operation failed; raw output suppressed')
  text=p.stdout.strip()
  if not text:return None
  try:return json.loads(text)
  except Exception:return text
 def get(self,kind,name=None,namespace=None,optional=False):
  args=['get',kind]+([name] if name else [])+(['-n',namespace] if namespace else [])+(['--ignore-not-found'] if optional else [])+['-o','json']
  return self.call(args)
 def apply(self,docs):
  return self.call(['apply','-f','-','-o','json'],json.dumps({'apiVersion':'v1','kind':'List','items':docs}))
 def create(self,doc):
  return self.call(['create','-f','-','-o','json'],json.dumps(doc))

def reviewed_refreeze_handoff(c, existing, source, jobs):
 """Allow only the approved 1483 successor to reuse its existing Namespace."""
 lane=c.get('tagged_lane') or {}; meta=existing.get('metadata', {}); labels=meta.get('labels', {})
 return (c.get('mode')=='prepare' and c.get('environment')=='sit' and
         c.get('candidate_sha')=='ca22f0506cd77827e04c81d4f99af859ecdf2c0c' and
         lane=={'feature_id':'sit-test-1483','feature_tag':'verified-sit-test-1483',
                'candidate_sha256':'57b7c8027888196b8f84b33f7b4d0ad96b0c801d31f5a60cc7f8f04b80d1be8e'} and
         meta.get('name')=='feature-sit-test-1483-sit' and
         meta.get('uid')=='b3d9b516-eb7c-4e7d-ba81-470b4d4cacc6' and
         isinstance(meta.get('resourceVersion'),str) and bool(meta['resourceVersion']) and
         not meta.get('deletionTimestamp') and
         labels.get('app.kubernetes.io/managed-by')=='dev-feature-flux-owner' and
         labels.get('environment')=='sit' and labels.get('release.svc.plus/feature')=='sit-test-1483' and
         labels.get('feature_id')=='sit-test-1483' and labels.get('release.svc.plus/feature-tag')=='smoke-sit-test-1483' and
         source.get('spec',{}).get('ref',{}).get('commit')=='8cae3959b4539257db39579dc9c7c173989d277f' and
         isinstance(jobs.get('items'),list) and all(not job.get('status',{}).get('active',0) for job in jobs['items']))


def handoff_namespace(client, namespace, existing, new_tag):
 meta=existing['metadata']
 ops=[{'op':'test','path':'/metadata/uid','value':meta['uid']},
      {'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},
      {'op':'test','path':'/metadata/labels/release.svc.plus~1feature-tag','value':'smoke-sit-test-1483'},
      {'op':'replace','path':'/metadata/labels/release.svc.plus~1feature-tag','value':new_tag}]
 client.call(['patch','namespace',namespace,'--type=json','-p',json.dumps(ops),'-o','json'])


def rebuild_namespace(c,client,existing,sleep,clock):
 old_uid=c['namespace_uid']
 require(c['environment']=='sit','only an isolated SIT lane may be rebuilt')
 pv_names=[]
 if existing:
  require(existing['metadata'].get('uid')==old_uid,'SIT namespace incarnation changed before rebuild')
  labels=existing['metadata'].get('labels',{})
  require(labels.get('app.kubernetes.io/managed-by')=='dev-feature-flux-owner' and labels.get('release.svc.plus/feature')==FEATURE and labels.get('feature_id')==FEATURE and labels.get('environment')=='sit','namespace ownership mismatch; refuse rebuild')
  claims=client.get('persistentvolumeclaims',namespace=NS).get('items',[])
  for claim in claims:
   spec=claim.get('spec',{});volume=spec.get('volumeName')
   if not volume:
    require(claim.get('status',{}).get('phase') in ('Pending',None),'unbound claim has an unexpected phase')
    continue
   pv=client.get('persistentvolume',volume);pv_spec=pv.get('spec',{});claim_ref=pv_spec.get('claimRef',{})
   require(claim_ref.get('namespace')==NS and claim_ref.get('name')==claim['metadata']['name'] and claim_ref.get('uid')==claim['metadata']['uid'],'persistent volume claim ownership mismatch')
   require(pv_spec.get('storageClassName')=='local-path' and pv_spec.get('persistentVolumeReclaimPolicy')=='Delete','SIT rebuild cannot safely reclaim this persistent volume')
   pv_names.append(volume)
  claimed=[pv['metadata']['name'] for pv in client.get('persistentvolumes').get('items',[]) if pv.get('spec',{}).get('claimRef',{}).get('namespace')==NS]
  unmatched=set(claimed)-set(pv_names)
  if existing['metadata'].get('deletionTimestamp'):
   for name in sorted(unmatched):
    pv=client.get('persistentvolume',name);pv_spec=pv.get('spec',{})
    require(pv_spec.get('storageClassName')=='local-path' and pv_spec.get('persistentVolumeReclaimPolicy')=='Delete','in-progress SIT deletion references a volume that cannot be safely reclaimed')
    pv_names.append(name)
  else:
   require(not unmatched,'unmatched persistent volume references the SIT namespace')
 else:
  claimed=[pv['metadata']['name'] for pv in client.get('persistentvolumes').get('items',[]) if pv.get('spec',{}).get('claimRef',{}).get('namespace')==NS]
  require(not claimed,'orphaned persistent volume still references the deleted SIT namespace')
 timeout=max(60,min(int(c.get('rebuild_wait_seconds',300)),600));deadline=clock()+timeout
 if existing and not existing['metadata'].get('deletionTimestamp'):
  client.call(['delete','namespace',NS,'--wait=false'])
 if existing:
  while True:
   current=client.get('namespace',NS,optional=True)
   if current is None:break
   require(current['metadata'].get('uid')==old_uid,'namespace name was recreated by another actor during deletion')
   if clock()>=deadline:raise ValueError('namespace deletion timed out; no force-finalizer removal')
   sleep(5)
 while pv_names:
  remaining=[name for name in pv_names if client.get('persistentvolume',name,optional=True) is not None]
  if not remaining:break
  if clock()>=deadline:raise ValueError('SIT local-path volume reclamation timed out; namespace remains deleted and rebuild is blocked')
  sleep(5)
 claimed=[pv['metadata']['name'] for pv in client.get('persistentvolumes').get('items',[]) if pv.get('spec',{}).get('claimRef',{}).get('namespace')==NS]
 require(not claimed,'orphaned persistent volume still references the deleted SIT namespace')
 docs=namespace_documents(c)
 require(client.get('namespace',NS,optional=True) is None,'namespace name was claimed during rebuild; refusing adoption')
 client.create(docs[0])
 client.apply(docs[1:])
 current=client.get('namespace',NS)
 new_uid=current.get('metadata',{}).get('uid','')
 require(UUID.fullmatch(new_uid) and new_uid!=old_uid,'recreated SIT namespace UID did not change')
 labels=current.get('metadata',{}).get('labels',{})
 require(labels.get('app.kubernetes.io/managed-by')=='dev-feature-flux-owner' and labels.get('release.svc.plus/feature')==FEATURE and labels.get('feature_id')==FEATURE and labels.get('environment')=='sit','recreated SIT namespace labels mismatch')
 return {'namespace_uid':new_uid,'rebuild':{'previous_namespace_uid':old_uid,'old_local_path_pvs':pv_names,'namespace_deleted':True,'persistent_volumes_reclaimed':True}}

def execute(c,client=None,sleep=time.sleep,clock=time.monotonic):
 validate(c);require(re.fullmatch(r'[1-9][0-9]*',str(c.get('instance_id',''))),'CMDB instance identity required')
 client=client or Client()
 NS,SOURCE,PATH,feature,domains,tag=scope(c)
 def base():
  source,root=('platform-config-dev','platform-ingress-dev') if tag is not None else ('platform-config','platform-root')
  return (client.get('nodes'),client.get('namespace','kube-system'),client.get('gitrepository',source,'flux-system'),client.get('kustomization',root,'flux-system'))
 baseline=base();preflight(c,*baseline)
 existing=client.get('namespace',NS,optional=True)
 handoff=False
 if existing:
  require(not existing['metadata'].get('deletionTimestamp'),'namespace is terminating')
  if c['mode']=='verify':require(existing['metadata'].get('uid')==c.get('namespace_uid'),'namespace incarnation differs from authenticated prepare')
  labels=existing['metadata'].get('labels',{})
  owned=labels.get('app.kubernetes.io/managed-by')=='dev-feature-flux-owner' and labels.get('release.svc.plus/feature')==feature and labels.get('environment')==c['environment']
  tag_matches=tag is None or (labels.get('feature_id')==feature and labels.get('release.svc.plus/feature-tag')==tag)
  if owned and not tag_matches and c['mode']=='prepare':
   previous=client.get('gitrepository',SOURCE,NS,optional=True) or {}
   jobs=client.get('jobs',namespace=NS) or {}
   handoff=reviewed_refreeze_handoff(c,existing,previous,jobs)
  require(owned and (tag_matches or handoff),'existing namespace has another owner; no takeover')
 if c['mode']=='verify':require(existing is not None and UUID.fullmatch(c.get('namespace_uid','')),'verified live namespace UID required')
 report={'namespace_uid':existing['metadata'].get('uid') if existing else None,'mode':c['mode'],'schema_version':'dev-feature-flux/v1','result':'planned','changed':False,'namespace':NS,'node_name':NODE,'node_uid':c['node_uid'],'cluster_uid':c['cluster_uid'],'candidate_sha':c['candidate_sha'],'manifest_sha256':c['manifest_sha256'],'owner_sha':c['owner_sha'],'platform_sha':c['platform_sha'],'business_acceptance':'NOT_RUN','application_secret_values_read':False,'audit_acceptance':'NOT_RUN','business_uid_acceptance':'NOT_RUN'}
 if tag is not None:
  report.update(feature_id=feature,feature_tag=tag,candidate_sha256=c['tagged_lane']['candidate_sha256'],lane_path=PATH,ingress_revision=baseline[3]['status']['lastAppliedRevision'])
 if c['mode']=='plan':return report
 if handoff:
  handoff_namespace(client,NS,existing,tag)
  current=client.get('namespace',NS)
  require(current.get('metadata',{}).get('uid')==existing['metadata']['uid'] and current.get('metadata',{}).get('labels',{}).get('release.svc.plus/feature-tag')==tag,'namespace handoff verification failed')
  report['handoff']={'previous_feature_tag':'smoke-sit-test-1483','namespace_uid':existing['metadata']['uid'],'previous_candidate_sha':'8cae3959b4539257db39579dc9c7c173989d277f'}
 if c['mode']=='rebuild':
  report.update(rebuild_namespace(c,client,existing,sleep,clock))
  preflight(c,*base())
  report.update(mode='rebuild',result='prepared',changed=True)
  return report
 if c['mode']=='verify':
  seed=c.get('seed_receipt',{})
  require(seed.get('schema_version')=='dev-lane-seed/v1' and seed.get('result')=='verified' and seed.get('namespace')==NS and seed.get('namespace_uid')==c.get('namespace_uid') and seed.get('candidate_sha')==c['candidate_sha'] and seed.get('node_uid')==c['node_uid'] and seed.get('cluster_uid')==c['cluster_uid'] and seed.get('instance_id')==c['instance_id'] and seed.get('database_roles')==['xc_accounts_rw','xc_billing_rw'] and seed.get('schema_initialized') is True,'independent namespace seed owner receipt required before application reconciliation')
  if tag is not None:
   require(seed.get('feature_id')==feature and seed.get('feature_tag')==tag and seed.get('candidate_sha256')==c['tagged_lane']['candidate_sha256'],'tagged seed receipt differs from candidate')
   verify_seed_baseline(seed,c)
 if tag is not None and c['mode']=='submit':
  proof=c.get('dns_http_proof',[])
  try:target=ipaddress.IPv4Address(c.get('target_ip',''))
  except (ipaddress.AddressValueError,TypeError):raise ValueError('valid public Dev ingress address required') from None
  require(target.is_global,'valid public Dev ingress address required')
  require({x.get('host') for x in proof}==domains and all(x.get('addresses')==[c.get('target_ip')] and x.get('http_status') in (200,404,308) and (x.get('http_status')!=308 or x.get('redirect_location')=='https://'+x['host']+'/.well-known/acme-challenge/owner-preflight') for x in proof),'fresh tagged DNS/HTTP evidence required before Caddy ingress')
 if c['mode']=='enable-tls':
  require(existing is not None,'prepare the namespace first')
  proof=c.get('dns_http_proof',[])
  require({x.get('host') for x in proof}==domains and all(x.get('addresses')==[c.get('target_ip')] and x.get('http_status') in (200,404) for x in proof),'fresh external DNS/HTTP evidence required before TLS')
  original=client.get('kustomization',SOURCE,NS);live=client.get('gitrepository',SOURCE,NS)
  require(ready(original) and revision(original.get('status',{}).get('lastAppliedRevision'),c['candidate_sha']) and ready(live),'base lane must be the exact Ready candidate before TLS')
 try:
  if c['mode']!='verify':client.apply(namespace_documents(c) if c['mode']=='prepare' else documents(c))
 except Exception:
  report.update(changed='UNKNOWN',result='apply_failed_or_unknown');return report
 report.update(changed=c['mode']!='verify',result='applied_unverified')
 if c['mode']=='submit':
  preflight(c,*base());report.update(result='submitted_pending_seed',business_acceptance='NOT_RUN');return report
 if c['mode']=='prepare':
  current=client.get('namespace',NS)
  require(current and not current['metadata'].get('deletionTimestamp') and UUID.fullmatch(current['metadata'].get('uid','')),'live prepared namespace UID missing')
  if existing:require(current['metadata']['uid']==existing['metadata']['uid'],'namespace changed during preparation')
  preflight(c,*base());report.update(result='prepared',namespace_uid=current['metadata']['uid']);return report
 deadline=clock()+int(c.get('wait_seconds',900))
 while True:
  source=client.get('gitrepository',SOURCE,NS,optional=True)
  root=client.get('kustomization',SOURCE+('-tls' if c['mode']=='enable-tls' else ''),NS,optional=True)
  release=client.get('helmrelease','distributed-web-saas-fullstack',NS,optional=True)
  if source and root and release and ready(source) and ready(root) and ready(release) and revision(source.get('status',{}).get('artifact',{}).get('revision'),c['candidate_sha']) and revision(root.get('status',{}).get('lastAppliedRevision'),c['candidate_sha']):
   require(source.get('spec',{}).get('ref')==({'branch':'main'} if c['environment']=='uat' else {'commit':c['candidate_sha']}),'candidate source drifted')
   preflight(c,*base())
   if c['mode']=='verify':
    current=client.get('namespace',NS)
    require(current['metadata'].get('uid')==c['namespace_uid'] and not current['metadata'].get('deletionTimestamp'),'namespace changed during runtime verification')
    runtime_pods=client.get('pods',namespace=NS)
    report['runtime_images']=verify_runtime(c,runtime_pods)
    if tag=='mail-sit-test-1483':report['sit_mail_sink']=verify_sit_mail_sink(c,runtime_pods,client)
   report.update(result=('runtime_ready' if tag is not None and c['mode']=='verify' else 'verified'),source_revision=source['status']['artifact']['revision'],applied_revision=root['status']['lastAppliedRevision'],helm_ready=True,tls_certificate_acceptance='NOT_RUN',business_acceptance='NOT_RUN',watch_main=c['environment']=='uat',observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
   return report
  if clock()>=deadline:
   report.update(result='not_ready',source_ready=bool(source and ready(source)),kustomization_ready=bool(root and ready(root)),helm_ready=bool(release and ready(release)))
   return report
  sleep(5)

def successful_result(result):
 return result in ('planned','prepared','submitted_pending_seed','verified','runtime_ready')


if __name__=='__main__':
 try:
  result=execute(json.loads(os.environ['DEV_FEATURE_CONFIG']));print(json.dumps(result,sort_keys=True))
  raise SystemExit(0 if successful_result(result['result']) else 1)
 except Exception as error:
  config={}
  try:config=json.loads(os.environ.get('DEV_FEATURE_CONFIG','{}'))
  except Exception:pass
  print(json.dumps({'result':'blocked_or_failed','error_type':type(error).__name__,'failure_code':failure_code(config.get('mode','unknown'),error),'business_acceptance':'NOT_RUN'}));raise SystemExit(2)
