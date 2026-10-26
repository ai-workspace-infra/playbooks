#!/usr/bin/env python3
"""Dev feature CI owner adapter using the existing Dev SSH record, never PR code."""
import argparse,datetime,hashlib,http.client,json,os,re,socket,subprocess,tempfile
from pathlib import Path
import dev_feature_candidate as candidate
from dev_k3s_bootstrap import request,validate_session,validate_cmdb

ROOT=Path(__file__).resolve().parents[1]
ROLE='github-actions-playbooks-dev-k3s-bootstrap'
RECORD='kv/data/CICD/dev/k3s-bootstrap'
SIT_CANDIDATE='4a7fb32a9f6ba541ba5649b43e37f5298fab30df'

def require(value,message):
 if not value:raise ValueError(message)

def run(args,env=None,stdin=None,timeout=1200):
 p=subprocess.run(args,input=stdin,text=True,capture_output=True,env=env,timeout=timeout)
 require(p.returncode==0,'owner process failed: '+Path(args[0]).name)
 return p.stdout

def execute(a):
 require(a.stage in candidate.SCOPE.SCOPES and a.mode in ('plan','prepare','submit','verify','enable-tls'),'explicit lane/mode required')
 if a.stage=='sit':require(a.gitops_sha==SIT_CANDIDATE,'SIT must use the reviewed fixed candidate; update owner review for a new candidate')
 require(candidate.SCOPE.SHA.fullmatch(a.gitops_sha) and candidate.SCOPE.SHA.fullmatch(a.platform_sha),'full GitOps SHA required')
 require(re.fullmatch(r'[0-9a-f]{64}',a.cmdb_sha256),'CMDB digest required')
 require(hashlib.sha256(a.cmdb.read_bytes()).hexdigest()==a.cmdb_sha256,'CMDB checksum mismatch')
 cmdb=validate_cmdb(json.loads(a.cmdb.read_text()),a.instance_id)
 observation=candidate.validate_tree(a.gitops_root,a.gitops_sha,a.stage,a.mode=='enable-tls')
 owner_sha=run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()
 config={'mode':a.mode,'environment':a.stage,'namespace':observation['namespace'],'node_name':'dev-k3s-v5','node_uid':a.node_uid,'cluster_uid':a.cluster_uid,'candidate_sha':a.gitops_sha,'platform_sha':a.platform_sha,'owner_sha':owner_sha,'manifest_sha256':observation['manifest_sha256'],'wait_seconds':900}
 if a.mode=='enable-tls':
  config['target_ip']=cmdb['dev-k3s-v5']['ip'];proof=[]
  for host in sorted(candidate.SCOPE.DOMAINS[a.stage]):
   addresses=sorted(set(socket.gethostbyname_ex(host)[2]))
   require(addresses==[config['target_ip']],'DNS does not point uniquely to this Dev instance')
   connection=http.client.HTTPConnection(host,80,timeout=10)
   try:
    connection.request('GET','/.well-known/acme-challenge/owner-preflight')
    status=connection.getresponse().status
   finally:connection.close()
   require(status in (200,404),'HTTP challenge entrypoint not reachable without redirect')
   proof.append({'host':host,'addresses':addresses,'http_status':status})
  config['dns_http_proof']=proof
 candidate.SCOPE.validate(config)
 if a.mode=='verify':
  require(a.seed_receipt is not None and re.fullmatch(r'[0-9a-f]{64}',a.seed_sha256 or ''),'authenticated seed receipt artifact/digest required')
  require(hashlib.sha256(a.seed_receipt.read_bytes()).hexdigest()==a.seed_sha256,'seed receipt digest mismatch')
  config['seed_receipt']=json.loads(a.seed_receipt.read_text())
 a.receipt.unlink(missing_ok=True)
 oidc=request(os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']+'&audience=vault',{'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})['value']
 session=request('https://vault.svc.plus/v1/auth/jwt/login',{'Content-Type':'application/json'},json.dumps({'role':ROLE,'jwt':oidc}).encode())['auth']
 validate_session(session);token=session['client_token'];agent=None
 try:
  secret=request('https://vault.svc.plus/v1/'+RECORD,{'X-Vault-Token':token})['data']['data']
  require(str(secret.get('INSTANCE_ID'))==a.instance_id,'Dev SSH binding instance mismatch')
  require(secret.get('SSH_PRIVATE_KEY') and secret.get('SSH_KNOWN_HOSTS') and secret.get('SSH_PUBLIC_KEY') and secret.get('SSH_USER')=='root','fixed Dev management binding required')
  output=run(['ssh-agent','-s'])
  agent={**os.environ,**{k:re.search(rf'{k}=([^;]+);',output).group(1) for k in ('SSH_AUTH_SOCK','SSH_AGENT_PID')}}
  run(['ssh-add','-'],agent,secret['SSH_PRIVATE_KEY']+'\n')
  with tempfile.TemporaryDirectory(prefix='dev-feature-') as directory:
   d=Path(directory)
   known=d/'known_hosts';known.write_text(secret['SSH_KNOWN_HOSTS']);known.chmod(0o600)
   ip=cmdb['dev-k3s-v5']['ip']
   run(['ssh-keygen','-F',ip,'-f',str(known)])
   public=d/'identity.pub';public.write_text(secret['SSH_PUBLIC_KEY']+'\n')
   args=f'-F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known} -o GlobalKnownHostsFile=/dev/null -o UpdateHostKeys=no -o IdentityFile={public} -o IdentitiesOnly=yes -o BatchMode=yes'
   # This projection excludes arbitrary Ansible connection/interpreter overrides.
   host=cmdb['dev-k3s-v5'];host['ansible_user']='root';host['host_vars']['ansible_ssh_common_args']=args
   selected=d/'cmdb.json';selected.write_text(json.dumps(cmdb));selected.chmod(0o600)
   report=d/'runtime.json'
   variables=d/'vars.json';variables.write_text(json.dumps({'dev_feature_target':'dev-k3s-v5','dev_feature_config':config,'dev_feature_apply':a.mode!='plan','dev_feature_report':str(report)}))
   env={**agent,'AI_WORKSPACE_CMDB_JSON':str(selected),'ANSIBLE_HOST_KEY_CHECKING':'True','ANSIBLE_SSH_ARGS':args,'ANSIBLE_ROLES_PATH':str(ROOT/'roles')}
   if a.mode=='prepare':env.update(DEV_SYNC_VAULT_TOKEN=token,DEV_REGISTRY_HOST=ip,DEV_REGISTRY_KNOWN_HOSTS=str(known),DEV_REGISTRY_PUBLIC_IDENTITY=str(public))
   inventory=str(ROOT/'inventory/terraform_cmdb.py')
   play=['ansible-playbook','-i',inventory,str(ROOT/'dev_feature_flux.yml'),'--limit','dev-k3s-v5','-e','@'+str(variables)]
   run([*play,'--syntax-check'],env)
   p=subprocess.run(play,env=env,text=True,capture_output=True,timeout=1200)
   require(report.is_file(),'no runtime owner receipt')
   runtime=json.loads(report.read_text())
   runtime.update(instance_id=a.instance_id,cmdb_sha256=a.cmdb_sha256,candidate=observation,run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
   a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(runtime,sort_keys=True)+'\n')
   require(p.returncode==0 and runtime.get('result')=={'plan':'planned','prepare':'prepared','submit':'submitted_pending_seed','verify':'verified','enable-tls':'verified'}[a.mode],'namespace deployment did not verify')
 finally:
  if agent:subprocess.run(['ssh-agent','-k'],env=agent,capture_output=True)
  if token and session.get('token_type')!='batch':request('https://vault.svc.plus/v1/auth/token/revoke-self',{'X-Vault-Token':token},b'{}')

def main():
 p=argparse.ArgumentParser()
 for name in ('stage','mode','instance-id','gitops-sha','platform-sha','cmdb-sha256','node-uid','cluster-uid'):p.add_argument('--'+name,required=True)
 for name in ('cmdb','gitops-root','receipt'):p.add_argument('--'+name,required=True,type=Path)
 p.add_argument('--seed-receipt',type=Path);p.add_argument('--seed-sha256')
 execute(p.parse_args())
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Dev feature owner rejected scope or execution ('+type(error).__name__+'); raw output suppressed') from None
