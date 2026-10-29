"""Existing Dev management binding shared by narrowly scoped owner adapters."""
import copy,json,os,re,subprocess,tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from dev_k3s_bootstrap import request,validate_session

ROLE='github-actions-playbooks-dev-k3s-bootstrap'
RECORD='kv/data/CICD/dev/k3s-bootstrap'

def require(value,message):
 if not value:raise ValueError(message)

def run(args,env=None,stdin=None,timeout=1200):
 p=subprocess.run(args,input=stdin,text=True,capture_output=True,env=env,timeout=timeout)
 require(p.returncode==0,'owner process failed: '+Path(args[0]).name)
 return p.stdout

@contextmanager
def binding(cmdb,instance_id,tagged=False):
 oidc=request(os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']+'&audience=vault',{'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})['value']
 role='github-actions-playbooks-feature-lane-sit' if tagged else ROLE
 session=request('https://vault.svc.plus/v1/auth/jwt/login',{'Content-Type':'application/json'},json.dumps({'role':role,'jwt':oidc}).encode())['auth']
 validate_session(session);token=session['client_token'];agent=None
 try:
  secret=request('https://vault.svc.plus/v1/'+RECORD,{'X-Vault-Token':token})['data']['data']
  require(str(secret.get('INSTANCE_ID'))==instance_id,'Dev SSH binding instance mismatch')
  require(secret.get('SSH_PRIVATE_KEY') and secret.get('SSH_KNOWN_HOSTS') and secret.get('SSH_PUBLIC_KEY') and secret.get('SSH_USER')=='root','fixed Dev management binding required')
  output=run(['ssh-agent','-s'])
  agent={**os.environ,**{k:re.search(rf'{k}=([^;]+);',output).group(1) for k in ('SSH_AUTH_SOCK','SSH_AGENT_PID')}}
  run(['ssh-add','-'],agent,secret['SSH_PRIVATE_KEY']+'\n')
  with tempfile.TemporaryDirectory(prefix='dev-owner-') as directory:
   d=Path(directory);known=d/'known_hosts';known.write_text(secret['SSH_KNOWN_HOSTS']);known.chmod(0o600)
   selected_cmdb=copy.deepcopy(cmdb);ip=selected_cmdb['dev-k3s-v5']['ip']
   run(['ssh-keygen','-F',ip,'-f',str(known)])
   public=d/'identity.pub';public.write_text(secret['SSH_PUBLIC_KEY']+'\n')
   args=f'-F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known} -o GlobalKnownHostsFile=/dev/null -o UpdateHostKeys=no -o IdentityFile={public} -o IdentitiesOnly=yes -o BatchMode=yes'
   host=selected_cmdb['dev-k3s-v5'];host['ansible_user']='root';host['host_vars']['ansible_ssh_common_args']=args
   selected=d/'cmdb.json';selected.write_text(json.dumps(selected_cmdb));selected.chmod(0o600)
   env={**agent,'AI_WORKSPACE_CMDB_JSON':str(selected),'ANSIBLE_HOST_KEY_CHECKING':'True','ANSIBLE_SSH_ARGS':args}
   yield SimpleNamespace(directory=d,env=env,token=token,host=ip,known=known,public=public)
 finally:
  if agent:subprocess.run(['ssh-agent','-k'],env=agent,capture_output=True)
  if token and session.get('token_type')!='batch':request('https://vault.svc.plus/v1/auth/token/revoke-self',{'X-Vault-Token':token},b'{}')
