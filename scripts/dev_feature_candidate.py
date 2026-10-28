#!/usr/bin/env python3
"""Offline validation of a fixed GitOps lane; never executes repository scripts."""
import hashlib,importlib.util,json,re,subprocess
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lane_scope',ROOT/'roles/platform/dev_feature_flux/files/reconcile.py')
SCOPE=importlib.util.module_from_spec(spec);spec.loader.exec_module(SCOPE)
CM={'web-saas-fullstack-values','web-saas-fullstack-release','web-saas-accounts-template','web-saas-console-nginx'}
IMAGE=re.compile(r'^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')

def require(value,message):
 if not value:raise ValueError(message)

def image_set(release):
 paths={'caddy':('caddy','image'),'postgresql':('postgresql','image'),'console':('console','singleContainer','image'),'accounts':('accounts','workload','image'),'billing':('billing','workload','image'),'content':('content','workload','image')}
 images={}
 repos={'caddy':'ai-workspace-infra/caddy-cloudflare','postgresql':'ai-workspace-services/postgresql','console':'ai-workspace-services/console','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/docs'}
 for name,path in paths.items():
  value=release
  for key in path:value=value[key]
  require(isinstance(value,str) and IMAGE.fullmatch(value),'six-component immutable image digest required')
  require(value.startswith('ghcr.io/'+repos[name]+'@'),'component image repository mismatch')
  annotations=release[name].get('podAnnotations',{})
  source_repos={'caddy':'ai-workspace-infra/artifacts','postgresql':'ai-workspace-services/postgresql','console':'ai-workspace-services/portal','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/content-service'}
  require(annotations.get('release.svc.plus/source_repository')==source_repos[name] and SCOPE.SHA.fullmatch(str(annotations.get('release.svc.plus/source_sha',''))),'component source annotation mismatch')
  require(annotations.get('release.svc.plus/image_digest')==value.split('@',1)[1] and re.fullmatch(r'[0-9]+',str(annotations.get('release.svc.plus/build_run_id',''))),'image/build annotation mismatch')
  if name in ('accounts','billing','content'):require(release[name]['workload'].get('env',{}).get('IMAGE')==value,'runtime image annotation must match component image')
  images[name]=value
 require(len(set(images.values()))==6,'six distinct component images required')
 return images

RUNTIME_SECRETS={'accounts':'web-saas-accounts-runtime','billing':'web-saas-billing-runtime','console':'web-saas-console-runtime','content':'web-saas-content-runtime','postgresql':'web-saas-db-admin'}
FORBIDDEN_POD_OVERRIDES={'serviceAccountName','automountServiceAccountToken','serviceAccountToken','projected','csi','hostPath','hostNetwork','hostPID','hostIPC'}
def validate_values(values):
 require(isinstance(values,dict) and set(values)<={'global','edgeGateway','stunnelClient','stunnelServer','caddy','postgresql','console','accounts','billing','content'},'unsupported chart root extension')
 def walk(value,path=()):
  if isinstance(value,list):
   for item in value:walk(item,path)
  elif isinstance(value,dict):
   component=path[0] if path else None
   for key,child in value.items():
    require(key not in FORBIDDEN_POD_OVERRIDES,'PR cannot override Pod management identity or privileged volume scope')
    if key=='imagePullSecrets':require(path==('global',) and child==[{'name':'web-saas-registry'}],'only fixed registry pull credential allowed')
    if key=='existingSecret':require(path==('postgresql',) and child=='web-saas-db-admin','only PostgreSQL may mount DB admin credential')
    if key=='secretRefs':require(component in RUNTIME_SECRETS and child==[RUNTIME_SECRETS[component]],'component runtime secret reference mismatch')
    if key in ('secretRef','secretKeyRef'):require(component in RUNTIME_SECRETS and isinstance(child,dict) and child.get('name')==RUNTIME_SECRETS[component],'component secret reference escaped')
    if key=='secretName':require(component in RUNTIME_SECRETS and child==RUNTIME_SECRETS[component],'component secret volume escaped')
    walk(child,(*path,key))
 walk(values)

def validate_patch_scope(value):
 if isinstance(value,list):
  for child in value:validate_patch_scope(child)
 elif isinstance(value,dict):
  forbidden=FORBIDDEN_POD_OVERRIDES|{'secret','secretName','secretRef','secretKeyRef','secretRefs','imagePullSecrets'}
  require(not (set(value)&forbidden),'post-renderer cannot change identity or secret access')
  require(not (set(str(value.get('path','')).split('/'))&forbidden),'JSON patch cannot change identity or secret access')
  for child in value.values():validate_patch_scope(child)

def candidate_scope(stage,candidate):
 if candidate is None:
  require(stage in SCOPE.SCOPES,'unsupported lane stage')
  return SCOPE.SCOPES[stage][0],SCOPE.SCOPES[stage][2],SCOPE.DOMAINS[stage],SCOPE.CHART
 require(stage=='sit' and isinstance(candidate,dict) and set(candidate)=={'tagged_lane','chart_digest','components'},'authenticated tagged candidate projection required')
 ns,_,path,_,hosts,_=SCOPE.scope({'environment':stage,'tagged_lane':candidate['tagged_lane']})
 chart=candidate['chart_digest']
 require(isinstance(chart,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',chart),'fixed candidate chart digest required')
 require(isinstance(candidate['components'],dict) and set(candidate['components'])=={'caddy','postgresql','console','accounts','billing','content'},'six candidate components required')
 return ns,path,hosts,chart

def validate_documents(documents,stage,tls=False,candidate=None):
 require(stage in SCOPE.SCOPES,'unsupported lane stage')
 if candidate is not None:require(not tls,'tagged Caddy lane has no separate TLS ingress phase')
 ns,_,allowed_hosts,chart=candidate_scope(stage,candidate)
 keys=set();by_kind={}
 for doc in documents:
  require(isinstance(doc,dict),'invalid rendered document')
  kind=doc.get('kind');meta=doc.get('metadata',{});name=meta.get('name')
  require(kind in {'ConfigMap','OCIRepository','HelmRelease','NetworkPolicy','Ingress'},'cluster/secret/RBAC/nested source resources are forbidden')
  require(meta.get('namespace')==ns,'resource escaped selected namespace')
  require(isinstance(name,str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]*',name),'invalid resource name')
  require((kind,name) not in keys,'duplicate resource');keys.add((kind,name));by_kind.setdefault(kind,[]).append(doc)
  if kind=='ConfigMap':require(name in CM,'unexpected ConfigMap')
 if tls:
  require(set(by_kind)=={'Ingress'},'TLS subpath permits only its exact Ingress')
  images={};sources={}
 else:
  require({d['metadata']['name'] for d in by_kind.get('ConfigMap',[])}==CM,'four fixed ConfigMaps required')
  release=yaml.safe_load(next(d for d in by_kind['ConfigMap'] if d['metadata']['name']=='web-saas-fullstack-release')['data']['release.yaml'])
  validate_values(release)
  values=yaml.safe_load(next(d for d in by_kind['ConfigMap'] if d['metadata']['name']=='web-saas-fullstack-values')['data']['values.yaml'])
  validate_values(values)
  images=image_set(release)
  sources={name:{'repository':release[name]['podAnnotations']['release.svc.plus/source_repository'],'source_sha':release[name]['podAnnotations']['release.svc.plus/source_sha'],'build_run_id':str(release[name]['podAnnotations']['release.svc.plus/build_run_id'])} for name in images}
  if candidate is not None:
   observed={name:{'image':images[name],**sources[name]} for name in images}
   require(observed==candidate['components'],'rendered images or sources differ from authenticated candidate')
  oci=by_kind.get('OCIRepository',[])
  require(len(oci)==1 and oci[0]['metadata']['name']=='web-saas-fullstack-chart','single fixed chart source required')
  require(oci[0]['spec'].get('url')=='oci://ghcr.io/ai-workspace-infra/distributed-web-saas-fullstack' and oci[0]['spec'].get('ref')=={'digest':chart},'unreviewed chart source/digest rejected')
  hr=by_kind.get('HelmRelease',[])
  require(len(hr)==1 and hr[0]['metadata']['name']=='distributed-web-saas-fullstack','single fixed HelmRelease required')
  h=hr[0]['spec']
  require(h.get('releaseName')=='web-saas' and h.get('targetNamespace')==ns and h.get('serviceAccountName')=='lane-reconciler','Helm must use lane-scoped identity and target')
  require(h.get('chartRef')=={'kind':'OCIRepository','name':'web-saas-fullstack-chart'},'unexpected chart reference')
  require(not h.get('values'),'unreviewed inline Helm values rejected')
  require(not h.get('chart') and not h.get('kubeConfig') and not h.get('storageNamespace'),'cross-cluster/namespace Helm overrides rejected')
  require(h.get('valuesFrom')==[{'kind':'ConfigMap','name':'web-saas-fullstack-values','valuesKey':'values.yaml'},{'kind':'ConfigMap','name':'web-saas-fullstack-release','valuesKey':'release.yaml'}],'fixed values then immutable release overlay required')
  require(all(d.get('namespace',ns)==ns for d in h.get('dependsOn',[])),'cross-namespace dependency rejected')
  for renderer in h.get('postRenderers',[]):
   require(set(renderer)=={'kustomize'} and set(renderer['kustomize'])<={'patches'},'unsupported post-renderer')
   for patch in renderer['kustomize'].get('patches',[]):
    target=patch.get('target',{})
    parsed=yaml.safe_load(patch.get('patch',''))
    validate_patch_scope(parsed)
    def no_image_override(value):
     if isinstance(value,dict):
      require('image' not in value and not str(value.get('path','')).endswith('/image'),'post-rendered images bypass immutable release')
      for child in value.values():no_image_override(child)
     elif isinstance(value,list):
      for child in value:no_image_override(child)
    no_image_override(parsed)
    require(target.get('kind') in {'Deployment','StatefulSet','Job','Service','ConfigMap','NetworkPolicy'} and str(target.get('name','')).startswith('web-saas-'),'post-renderer escaped workload allowlist')
 ingresses=by_kind.get('Ingress',[])
 require({i['metadata']['name'] for i in ingresses}==({'web-saas-https'} if tls else {'web-saas-http'}),'fixed phase-specific Ingress required')
 hosts=set()
 for item in ingresses:
  annotations=item['metadata'].get('annotations',{})
  if candidate is not None:
   require(not annotations and item['spec'].get('ingressClassName')=='caddy' and not item['spec'].get('tls'),'tagged lane requires reviewed Caddy ingress without external TLS secrets')
  else:
   require(set(annotations)<= {'kubernetes.io/ingress.class','traefik.ingress.kubernetes.io/router.entrypoints','traefik.ingress.kubernetes.io/router.tls','traefik.ingress.kubernetes.io/router.tls.certresolver'},'unreviewed ingress annotation')
   require(annotations.get('kubernetes.io/ingress.class')=='dev-web-saas' and not item['spec'].get('ingressClassName'),'fixed isolated Traefik annotation required')
   if tls:require(annotations.get('traefik.ingress.kubernetes.io/router.tls.certresolver')=='dev-http01','fixed TLS resolver required')
   else:require(not annotations.get('traefik.ingress.kubernetes.io/router.tls.certresolver'),'ACME must not be enabled before DNS proof')
  for rule in item['spec'].get('rules',[]):
   require(rule.get('host') in allowed_hosts,'public hostname not in trusted onwalk.net lane allowlist')
   hosts.add(rule['host'])
   for path in rule.get('http',{}).get('paths',[]):
    require(path.get('backend',{}).get('service')=={'name':'web-saas-caddy-ingress','port':{'number':80}},'unexpected ingress backend')
  for entry in item['spec'].get('tls',[]):
   require(not entry.get('secretName') and set(entry.get('hosts',[]))<=allowed_hosts,'TLS secret dependency is not authorized')
 require(hosts==allowed_hosts,'incomplete lane host mapping')
 return {'namespace':ns,'images':images,'sources':sources,'resource_count':len(documents),'chart_digest':chart,'hosts':sorted(hosts)}

def validate_tree(root,sha,stage,tls=False,candidate=None):
 root=Path(root).resolve()
 actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
 require(actual==sha and SCOPE.SHA.fullmatch(sha),'checkout must equal reviewed full candidate SHA')
 if stage=='uat':
  require(subprocess.check_output(['git','-C',str(root),'rev-parse','origin/main'],text=True).strip()==sha,'UAT candidate must be the current main commit')
 prefix=root/'services/distributed-web-saas-fullstack'
 _,lane_path,_,_=candidate_scope(stage,candidate)
 source=root/(lane_path.removeprefix('./')+('/tls' if tls else ''))
 require(source.is_dir() and source.resolve().is_relative_to(prefix.resolve()),'fixed lane directory missing or escaped')
 # Reject executable generators, remote bases and local file escapes before build.
 pending=[source];seen=set()
 while pending:
  directory=pending.pop()
  if directory in seen:continue
  seen.add(directory)
  matches=[directory/n for n in ('kustomization.yaml','kustomization.yml','Kustomization') if (directory/n).is_file()]
  require(len(matches)==1,'one local Kustomization file required')
  f=matches[0]
  content=yaml.safe_load(f.read_text()) or {}
  require(not any(content.get(k) for k in ('generators','transformers','helmCharts','helmGlobals','components')),'unreviewed executable or remote generator')
  for value in content.get('resources',[]):
   require(isinstance(value,str) and '://' not in value and not value.startswith('/'),'remote/absolute resources rejected')
   target=(f.parent/value).resolve();require(target.is_relative_to(prefix.resolve()),'resource escaped component tree')
   if target.is_dir():pending.append(target)
  for generator in content.get('configMapGenerator',[]):
   require(not generator.get('envs') and not generator.get('literals'),'only reviewed ConfigMap files supported')
   for value in generator.get('files',[]):
    target=(f.parent/value.split('=',1)[-1]).resolve();require(target.is_relative_to(prefix.resolve()),'ConfigMap file escaped component tree')
 result=subprocess.run(['kubectl','kustomize',str(source),'--load-restrictor=LoadRestrictionsRootOnly'],capture_output=True,text=True,timeout=60)
 require(result.returncode==0,'candidate Kustomize build failed; raw output suppressed')
 docs=[d for d in yaml.safe_load_all(result.stdout) if d]
 observation=validate_documents(docs,stage,tls,candidate)
 observation['manifest_sha256']=hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest()
 return observation
