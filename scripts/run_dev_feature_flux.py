#!/usr/bin/env python3
"""Dev feature CI owner adapter using the existing Dev SSH record, never PR code."""
import argparse,datetime,hashlib,http.client,json,os,re,socket,subprocess,tempfile
from pathlib import Path
import dev_feature_candidate as candidate
from dev_k3s_bootstrap import validate_cmdb
from dev_ssh_binding import binding as dev_binding,run

ROOT=Path(__file__).resolve().parents[1]
ROLE='github-actions-playbooks-dev-k3s-bootstrap'
RECORD='kv/data/CICD/dev/k3s-bootstrap'

def require(value,message):
 if not value:raise ValueError(message)

def validate_candidate_receipt(receipt,sha,stage,run_id):
 require(receipt.get('schema_version')=='dev-feature-candidate/v1' and receipt.get('result')=='verified','verified control-plane candidate receipt required')
 require(receipt.get('repository')=='ai-workspace-infra/gitops' and receipt.get('stage')==stage and receipt.get('candidate_head')==sha,'candidate receipt target mismatch')
 require(type(receipt.get('candidate_pr')) is int and receipt['candidate_pr']>0,'GitOps PR identity required')
 require(receipt.get('base_ref')=='main' and candidate.SCOPE.SHA.fullmatch(receipt.get('base_sha','')),'main baseline required')
 source=receipt.get('source',{})
 require(source.get('workflow_ref')=='ai-workspace-infra/platform-ops-toolkit/.github/workflows/dev-feature-deploy.yml@refs/heads/main' and str(source.get('run_id'))==str(run_id) and run_id is not None,'same-run protected control-plane receipt required')
 require(receipt.get('checks')=='success' and receipt.get('same_repository') is True and receipt.get('main_ancestry_verified') is True,'control-plane gates not verified')
 return receipt

def execute(a):
 require(a.stage in candidate.SCOPE.SCOPES and a.mode in ('plan','prepare','submit','verify','enable-tls'),'explicit lane/mode required')
 require(candidate.SCOPE.SHA.fullmatch(a.gitops_sha) and candidate.SCOPE.SHA.fullmatch(a.platform_sha),'full GitOps SHA required')
 require(re.fullmatch(r'[0-9a-f]{64}',a.cmdb_sha256),'CMDB digest required')
 require(hashlib.sha256(a.cmdb.read_bytes()).hexdigest()==a.cmdb_sha256,'CMDB checksum mismatch')
 require(re.fullmatch(r'[0-9a-f]{64}',a.candidate_sha256),'candidate receipt digest required')
 require(hashlib.sha256(a.candidate_receipt.read_bytes()).hexdigest()==a.candidate_sha256,'candidate receipt checksum mismatch')
 verified_candidate=validate_candidate_receipt(json.loads(a.candidate_receipt.read_text()),a.gitops_sha,a.stage,os.environ.get('GITHUB_RUN_ID'))
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
 with dev_binding(cmdb,a.instance_id) as transport:
  d=transport.directory;report=d/'runtime.json'
  variables=d/'vars.json';variables.write_text(json.dumps({'dev_feature_target':'dev-k3s-v5','dev_feature_config':config,'dev_feature_apply':a.mode!='plan','dev_feature_report':str(report)}))
  env={**transport.env,'ANSIBLE_ROLES_PATH':str(ROOT/'roles')}
  if a.mode=='prepare':env.update(DEV_SYNC_VAULT_TOKEN=transport.token,DEV_REGISTRY_HOST=transport.host,DEV_REGISTRY_KNOWN_HOSTS=str(transport.known),DEV_REGISTRY_PUBLIC_IDENTITY=str(transport.public))
  inventory=str(ROOT/'inventory/terraform_cmdb.py')
  play=['ansible-playbook','-i',inventory,str(ROOT/'dev_feature_flux.yml'),'--limit','dev-k3s-v5','-e','@'+str(variables)]
  run([*play,'--syntax-check'],env)
  p=subprocess.run(play,env=env,text=True,capture_output=True,timeout=1200)
  require(report.is_file(),'no runtime owner receipt')
  runtime=json.loads(report.read_text())
  runtime.update(instance_id=a.instance_id,cmdb_sha256=a.cmdb_sha256,candidate=observation,candidate_control_plane=verified_candidate,candidate_receipt_sha256=a.candidate_sha256,run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
  a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(runtime,sort_keys=True)+'\n')
  require(p.returncode==0 and runtime.get('result')=={'plan':'planned','prepare':'prepared','submit':'submitted_pending_seed','verify':'verified','enable-tls':'verified'}[a.mode],'namespace deployment did not verify')

def main():
 p=argparse.ArgumentParser()
 for name in ('stage','mode','instance-id','gitops-sha','platform-sha','cmdb-sha256','node-uid','cluster-uid','candidate-sha256'):p.add_argument('--'+name,required=True)
 for name in ('cmdb','gitops-root','receipt','candidate-receipt'):p.add_argument('--'+name,required=True,type=Path)
 p.add_argument('--seed-receipt',type=Path);p.add_argument('--seed-sha256')
 execute(p.parse_args())
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Dev feature owner rejected scope or execution ('+type(error).__name__+'); raw output suppressed') from None
