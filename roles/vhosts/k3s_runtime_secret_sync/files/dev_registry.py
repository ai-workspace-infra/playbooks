#!/usr/bin/env python3
"""Fixed Dev registry projection; secret material exists only in process memory/stdin."""
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location("dev_registry_oci",Path(__file__).with_name("dev_oci.py"))
dev_oci=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(dev_oci)
import base64,json,os,subprocess,urllib.request

def validate_namespace(namespace,tagged_lane=None):
 if tagged_lane is None:
  if namespace not in ('feature-xconnect-referral-sit','web-saas-uat'):raise ValueError('Dev namespace required')
  return None
 path=Path(__file__).resolve().parents[3]/'platform/dev_feature_flux/files/reconcile.py'
 spec=importlib.util.spec_from_file_location('dev_feature_scope',path)
 scope=importlib.util.module_from_spec(spec);spec.loader.exec_module(scope)
 expected,_,_,feature,_,tag=scope.scope({'environment':'sit','tagged_lane':tagged_lane})
 if namespace!=expected:raise ValueError('tagged registry namespace mismatch')
 return feature,tag

def build(namespace,record,tagged_lane=None):
 validate_namespace(namespace,tagged_lane)
 username=record.get('GHCR_USERNAME');token=record.get('GHCR_TOKEN')
 if not isinstance(username,str) or not username or not isinstance(token,str) or not token:raise ValueError('Dev registry fields missing')
 auth=base64.b64encode((username+':'+token).encode()).decode()
 config=json.dumps({'auths':{'ghcr.io':{'username':username,'password':token,'auth':auth}}})
 return {'apiVersion':'v1','kind':'Secret','metadata':{'namespace':namespace,'name':'web-saas-registry','labels':{'app.kubernetes.io/managed-by':'dev-runtime-secret-sync'}},'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':base64.b64encode(config.encode()).decode()}}

IMAGES={'caddy':'ai-workspace-infra/caddy-cloudflare','console':'ai-workspace-services/console','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/docs','postgresql':'ai-workspace-services/postgresql'}
SOURCES={'caddy':'ai-workspace-infra/artifacts','console':'ai-workspace-services/portal','accounts':'ai-workspace-services/accounts','billing':'ai-workspace-services/billing-service','content':'ai-workspace-services/content-service','postgresql':'ai-workspace-services/postgresql'}
def validate_context(context):
 import re
 lane=context.get('tagged_lane')
 validate_namespace(context.get('namespace'),lane)
 if not re.fullmatch('[1-9][0-9]*',str(context.get('instance_id',''))):raise ValueError('exact Dev target required')
 for key in ('namespace_uid','node_uid','cluster_uid'):
  if not re.fullmatch('[0-9a-f-]{36}',context.get(key,'')):raise ValueError('runtime UID binding required')
 source_context=context.get('source',{})
 repo=source_context.get('repository');allowed=('ai-workspace-infra/platform-ops-toolkit','ai-workspace-infra/Platform-Orchestrator') if lane is not None else ('ai-workspace-infra/platform-ops-toolkit',)
 workflow='feature-lane-deploy.yml' if lane is not None else 'dev-feature-deploy.yml'
 if repo not in allowed or source_context.get('workflow_ref')!=repo+'/.github/workflows/'+workflow+'@refs/heads/main' or not re.fullmatch('[0-9a-f]{40}',source_context.get('workflow_sha','')) or not re.fullmatch('[0-9a-f]{40}',source_context.get('owner_sha','')) or not re.fullmatch('[1-9][0-9]*',str(source_context.get('run_id',''))) or not re.fullmatch('[1-9][0-9]*',str(source_context.get('run_attempt',''))):raise ValueError('prepare GHA source binding required')
 if not re.fullmatch('[0-9a-f]{64}',context.get('manifest_sha256','')):raise ValueError('candidate manifest binding required')
 if not re.fullmatch('[0-9a-f]{40}',context.get('candidate_sha','')):raise ValueError('immutable candidate required')
 images=context.get('images',{});sources=context.get('sources',{})
 if set(images)!=set(IMAGES) or set(sources)!=set(IMAGES):raise ValueError('six components required')
 for component,image_repo in IMAGES.items():
  source=sources[component];reference=images[component]
  if not re.fullmatch('ghcr.io/'+re.escape(image_repo)+'@sha256:[0-9a-f]{64}',reference) or source.get('repository')!=SOURCES[component] or not re.fullmatch('[0-9a-f]{40}',source.get('source_sha','')) or not re.fullmatch('[1-9][0-9]*',str(source.get('build_run_id',''))):raise ValueError('candidate source/image mismatch')

def mapping(context,record,inspect=dev_oci.inspect):
 validate_context(context)
 images=context['images'];sources=context['sources'];result={}
 for component,image_repo in IMAGES.items():
  source=sources[component];reference=images[component]
  value=inspect(reference,record['GHCR_USERNAME'],record['GHCR_TOKEN'],source['repository'],source['source_sha'])
  if value.get('source_label_match') is False or value.get('revision_label_match') is False:raise ValueError('OCI source label mismatch')
  result[component]={**value,'source_repository':source['repository'],'source_sha':source['source_sha'],'build_run_id':source['build_run_id']}
 return {'schema_version':'dev-oci-platform/v1','images':result,'binding':context}

def remote_command(namespace,uid,tagged_lane=None):
 import shlex
 identity=validate_namespace(namespace,tagged_lane)
 script="""import json,subprocess,sys
ns,uid,feature,tag=sys.argv[1:]
k=['/usr/local/bin/k3s','kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=20s']
def current():
 p=subprocess.run(k+['get','namespace',ns,'-o','json'],text=True,capture_output=True,timeout=30)
 if p.returncode:raise SystemExit(2)
 obj=json.loads(p.stdout)
 if obj['metadata'].get('uid')!=uid or obj['metadata'].get('deletionTimestamp'):raise SystemExit(2)
 if tag:
  labels=obj['metadata'].get('labels',{})
  if labels.get('app.kubernetes.io/managed-by')!='dev-feature-flux-owner' or labels.get('environment')!='sit' or labels.get('feature_id')!=feature or labels.get('release.svc.plus/feature')!=feature or labels.get('release.svc.plus/feature-tag')!=tag:raise SystemExit(2)
obj=json.load(sys.stdin)
if obj.get('kind')!='Secret' or obj.get('type')!='kubernetes.io/dockerconfigjson' or obj['metadata'].get('namespace')!=ns or obj['metadata'].get('name')!='web-saas-registry' or set(obj.get('data',{}))!={'.dockerconfigjson'}:raise SystemExit(2)
current()
p=subprocess.run(k+['apply','--server-side','--field-manager=dev-runtime-secret-sync','-f','-','-o','name'],input=json.dumps(obj),text=True,capture_output=True,timeout=30)
if p.returncode or p.stdout.strip()!='secret/web-saas-registry':raise SystemExit(2)
current()
print('secret/web-saas-registry')
"""
 return shlex.join(['python3','-c',script,namespace,uid,*(identity or ('',''))])

def main():
 ns=os.environ['DEV_REGISTRY_NAMESPACE'];host=os.environ['DEV_REGISTRY_HOST']
 import ipaddress
 ipaddress.ip_address(host)
 context=json.loads(os.environ['DEV_REGISTRY_CONTEXT'])
 if context.get('namespace')!=ns:raise ValueError('registry namespace mismatch')
 validate_context(context)
 req=urllib.request.Request('https://vault.svc.plus/v1/kv/data/CICD/dev/ghcr',headers={'X-Vault-Token':os.environ['DEV_SYNC_VAULT_TOKEN']})
 with urllib.request.urlopen(req,timeout=20) as response:record=json.load(response)['data']
 secret=build(ns,record['data'],context.get('tagged_lane'))
 platform_mapping=mapping(context,record['data'])
 secret['metadata']['annotations']={'delivery.svc.plus/namespace-uid':context['namespace_uid']}
 args=['ssh','-F','/dev/null','-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null','-o','UpdateHostKeys=no','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+os.environ['DEV_REGISTRY_KNOWN_HOSTS'],'-i',os.environ['DEV_REGISTRY_PUBLIC_IDENTITY'],'root@'+host,remote_command(ns,context['namespace_uid'],context.get('tagged_lane'))]
 p=subprocess.run(args,input=json.dumps(secret),text=True,capture_output=True,timeout=60)
 if p.returncode or p.stdout.strip()!='secret/web-saas-registry':raise RuntimeError('registry projection failed')
 print(json.dumps({'result':'configured','namespace':ns,'secret_name':'web-saas-registry','source_path':'kv/CICD/dev/ghcr','source_version':record['metadata']['version'],'oci_mapping':platform_mapping}))
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit('Dev registry projection failed; sensitive diagnostics suppressed') from None
