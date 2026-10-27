#!/usr/bin/env python3
"""Actual namespace read-only SA authentication and business PID1 UID observation."""
import base64,datetime,json,os,re,ssl,subprocess,urllib.error,urllib.parse,urllib.request
SCOPES={'sit':'feature-xconnect-referral-sit','uat':'web-saas-uat'}
NAMES={'accounts','billing','caddy','console','content','postgresql'}
KUBECTL=['/usr/local/bin/k3s','kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=20s']

def require(ok,message):
 if not ok:raise ValueError(message)

def command(args):
 p=subprocess.run([*KUBECTL,*args],text=True,capture_output=True,timeout=30)
 require(p.returncode==0 and len(p.stdout)<=32_000,'bounded management observation failed')
 return p.stdout.strip()

def token_claims(token,namespace,now):
 require(isinstance(token,str) and len(token)<20_000 and token.count('.')==2,'bounded token response required')
 # Token comes directly from the fixed authenticated local TokenRequest. This
 # decoding only checks TTL/subject; actual authentication is the TLS API call.
 raw=token.split('.')[1];claims=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
 require(claims.get('sub')==f'system:serviceaccount:{namespace}:lane-auditor','unexpected issued audit identity')
 require(type(claims.get('exp')) is int and 0<claims['exp']-now<=1200,'audit token lifetime outside bound')
 return claims

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('local API redirects forbidden')

class API:
 def __init__(self,token):
  # Ask the fixed management kubeconfig for only its public CA, not client keys.
  # K3s data-dir may differ from its default; no host symlink is required.
  encoded=command(['config','view','--minify','--raw','-o','jsonpath={.clusters[0].cluster.certificate-authority-data}'])
  require(0<len(encoded)<=32_000,'bounded local API CA required')
  ca=base64.b64decode(encoded,validate=True).decode('ascii')
  context=ssl.create_default_context(cadata=ca)
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),NoRedirect())
  self.token=token
 def request(self,path,method='GET',body=None,expected=200,content_type='application/json',read=True):
  require(path.startswith('/') and '://' not in path and '..' not in path,'fixed local API path required')
  headers={'Authorization':'Bearer '+self.token,'Content-Type':content_type,'Accept':'application/json'}
  data=None if body is None else json.dumps(body).encode()
  req=urllib.request.Request('https://127.0.0.1:6443'+path,data=data,headers=headers,method=method)
  try:
   with self.opener.open(req,timeout=20) as response:
    status=response.status
    require(status==expected,'audit API permission mismatch')
    if not read:return {'status':status}
    raw=response.read(1_000_001);require(len(raw)<=1_000_000,'audit status response exceeds bound')
    return json.loads(raw)
  except urllib.error.HTTPError as error:
   status=error.code;error.close()  # Never read forbidden Secret/CM/log bodies.
   require(status==expected,'audit API permission mismatch')
   return {'status':status}

def process_uid(text):
 pid=re.search(r'(?m)^Pid:\s+([0-9]+)\s*$',text)
 uid=re.search(r'(?m)^Uid:\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*$',text)
 require(pid is not None and pid[1]=='1' and uid is not None,'actual container PID1 identity missing')
 values=[int(x) for x in uid.groups()]
 require(all(x>0 for x in values),'business PID1 is root')
 return {'pid':1,'real_uid':values[0],'effective_uid':values[1],'saved_uid':values[2],'filesystem_uid':values[3]}

def execute(config,runtime,api=None,run=command,now=None):
 require(config.get('environment') in SCOPES and config.get('namespace')==SCOPES[config['environment']] and config.get('mode')=='verify','fixed verified lane required')
 require(runtime.get('result')=='verified' and set(runtime.get('runtime_images',{}))==NAMES,'verified six-container runtime prerequisite required')
 namespace=config['namespace'];source=runtime['runtime_images']
 require(runtime.get('namespace_uid')==config.get('namespace_uid') and re.fullmatch('[0-9a-f-]{36}',config.get('namespace_uid','')),'authenticated namespace UID required')
 def namespace_current():
  require(run(['get','namespace',namespace,'-o','jsonpath={.metadata.uid}'])==config['namespace_uid'],'namespace changed during audit')
 namespace_current()
 if api is None:
  token=run(['create','token','lane-auditor','-n',namespace,'--duration=600s'])
  token_claims(token,namespace,now if now is not None else int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
  api=API(token)
 who=api.request('/apis/authentication.k8s.io/v1/selfsubjectreviews','POST',{'apiVersion':'authentication.k8s.io/v1','kind':'SelfSubjectReview'},expected=201)
 username=who.get('status',{}).get('userInfo',{}).get('username')
 require(username==f'system:serviceaccount:{namespace}:lane-auditor','actual API audit identity mismatch')
 prefix='/api/v1/namespaces/'+namespace
 pods=api.request(prefix+'/pods')
 observed={p['metadata']['name']:p for p in pods.get('items',[])}
 uid_results={}
 for component,row in source.items():
  name=row['pod'];require(re.fullmatch('[a-z0-9][a-z0-9.-]*',name),'invalid observed pod name')
  require(name in observed and observed[name]['metadata']['uid']==row['pod_uid'],'audit/management pod observation changed')
  uid_before=run(['get','pod',name,'-n',namespace,'-o','jsonpath={.metadata.uid}'])
  require(uid_before==row['pod_uid'],'pod changed before UID probe')
  status=run(['exec','-n',namespace,name,'-c',component,'--','/bin/cat','/proc/1/status'])
  uid_results[component]=dict(process_uid(status),pod=name,pod_uid=row['pod_uid'])
  require(run(['get','pod',name,'-n',namespace,'-o','jsonpath={.metadata.uid}'])==row['pod_uid'],'pod changed after UID probe')
 allowed={'pods':len(observed)}
 for resource,path in [('services',prefix+'/services'),('deployments','/apis/apps/v1/namespaces/'+namespace+'/deployments'),('statefulsets','/apis/apps/v1/namespaces/'+namespace+'/statefulsets')]:
  allowed[resource]=len(api.request(path).get('items',[]))
 pod=urllib.parse.quote(source['accounts']['pod'],safe='')
 denied={}
 for key,path,method,body,ctype in [
  ('secret',prefix+'/secrets/web-saas-accounts-runtime','GET',None,'application/json'),
  ('configmap',prefix+'/configmaps/web-saas-fullstack-values','GET',None,'application/json'),
  ('patch_dry_run','/apis/apps/v1/namespaces/'+namespace+'/deployments/web-saas-accounts?dryRun=All','PATCH',{'metadata':{'annotations':{'audit.svc.plus/probe':'dry-run'}}},'application/merge-patch+json'),
  ('exec_no_upgrade',prefix+'/pods/'+pod+'/exec?command=true&container=accounts&stdin=false&stdout=false&stderr=false&tty=false','POST',None,'application/json'),
  ('log_no_lines',prefix+'/pods/'+pod+'/log?container=accounts&follow=false&tailLines=0','GET',None,'application/json')]:
  # No upgrade headers/streams are opened; unexpected exec permission fails
  # without executing a command. Patch is server dry-run; no writes persist.
  denied[key]=api.request(path,method,body,expected=403,content_type=ctype,read=False)['status']
 namespace_current()
 return {'namespace_uid':config['namespace_uid'],'audit_acceptance':'verified','business_uid_acceptance':'verified','audit':{'identity':username,'namespace':namespace,'allowed_status_reads':allowed,'denied':denied,'token_requested_seconds':600},'business_process_uid':uid_results,'k3s_runtime_rootless':'NOT_CLAIMED'}

if __name__=='__main__':
 try:
  value=json.loads(os.environ['DEV_FEATURE_AUDIT']);print(json.dumps(execute(value['config'],value['runtime']),sort_keys=True))
 except Exception as error:
  print(json.dumps({'audit_acceptance':'NOT_RUN_OR_FAILED','business_uid_acceptance':'NOT_RUN_OR_FAILED','error_type':type(error).__name__}));raise SystemExit(2)
