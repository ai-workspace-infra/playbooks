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

def validate_prepare_receipt(receipt,config,observation):
 require(receipt.get('schema_version')=='dev-feature-flux/v1' and receipt.get('result')=='prepared' and receipt.get('mode') in ('prepare','rebuild'),'successful prepare/rebuild producer required')
 for key in ('candidate_sha','namespace','namespace_uid','node_uid','cluster_uid','manifest_sha256'):
  require(receipt.get(key)==config[key],'prepare proof stale or wrong target: '+key)
 require(receipt.get('instance_id')==config['instance_id'],'prepare instance mismatch')
 registry=receipt.get('registry',{})
 require(registry.get('result')=='configured' and registry.get('namespace')==config['namespace'] and registry.get('source_path')=='kv/CICD/dev/ghcr' and registry.get('secret_name')=='web-saas-registry','fixed Dev registry prepare receipt required')
 mapping=registry.get('oci_mapping',{});binding=mapping.get('binding',{})
 require(mapping.get('schema_version')=='dev-oci-platform/v1' and binding.get('instance_id')==config['instance_id'],'OCI mapping producer required')
 for key in ('candidate_sha','namespace','namespace_uid','node_uid','cluster_uid','manifest_sha256'):
  require(binding.get(key)==config[key],'OCI mapping binding mismatch: '+key)
 require(binding.get('images')==observation['images'] and binding.get('sources')==observation['sources'],'candidate/image/source changed since prepare')
 if receipt.get('mode')=='rebuild':
  lifecycle=receipt.get('rebuild',{})
  require(candidate.SCOPE.UUID.fullmatch(lifecycle.get('previous_namespace_uid','')) and lifecycle.get('previous_namespace_uid')!=receipt.get('namespace_uid') and lifecycle.get('namespace_deleted') is True and lifecycle.get('persistent_volumes_reclaimed') is True,'verified SIT namespace rebuild evidence required')
 source=binding.get('source',{})
 require(source.get('repository')=='ai-workspace-infra/platform-ops-toolkit' and source.get('workflow_ref')=='ai-workspace-infra/platform-ops-toolkit/.github/workflows/dev-feature-deploy.yml@refs/heads/main' and candidate.SCOPE.SHA.fullmatch(source.get('workflow_sha','')) and source.get('owner_sha')==receipt.get('owner_sha') and str(source.get('run_id'))==str(receipt.get('run_id')) and str(source.get('run_attempt'))==str(receipt.get('run_attempt')),'prepare original GHA producer binding mismatch')
 records=mapping.get('images',{});require(set(records)==set(observation['images']),'six OCI mappings required')
 for name,image in observation['images'].items():
  row=records[name];expected=observation['sources'][name]
  require(row.get('image')==image and row.get('manifest_digest')==image.split('@')[1] and row.get('platform')=='linux/amd64' and re.fullmatch('sha256:[0-9a-f]{64}',row.get('platform_manifest_digest','')) and re.fullmatch('sha256:[0-9a-f]{64}',row.get('config_digest','')),'invalid OCI mapping')
  require(row.get('source_repository')==expected['repository'] and row.get('source_sha')==expected['source_sha'] and str(row.get('build_run_id'))==expected['build_run_id'] and row.get('source_label_match') in (True,'UNKNOWN') and row.get('revision_label_match') in (True,'UNKNOWN'),'OCI mapping source mismatch')
 return mapping

def execute(a):
 require(a.stage in candidate.SCOPE.SCOPES and a.mode in ('plan','prepare','rebuild','submit','verify','enable-tls'),'explicit lane/mode required')
 if a.mode=='rebuild':require(a.stage=='sit' and a.prepare_receipt is not None and re.fullmatch(r'[0-9a-f]{64}',a.prepare_sha256 or ''),'authenticated prior SIT prepare receipt required for rebuild')
 require(candidate.SCOPE.SHA.fullmatch(a.gitops_sha) and candidate.SCOPE.SHA.fullmatch(a.platform_sha),'full GitOps SHA required')
 require(re.fullmatch(r'[0-9a-f]{64}',a.cmdb_sha256),'CMDB digest required')
 require(hashlib.sha256(a.cmdb.read_bytes()).hexdigest()==a.cmdb_sha256,'CMDB checksum mismatch')
 require(re.fullmatch(r'[0-9a-f]{64}',a.candidate_sha256),'candidate receipt digest required')
 require(hashlib.sha256(a.candidate_receipt.read_bytes()).hexdigest()==a.candidate_sha256,'candidate receipt checksum mismatch')
 verified_candidate=validate_candidate_receipt(json.loads(a.candidate_receipt.read_text()),a.gitops_sha,a.stage,os.environ.get('GITHUB_RUN_ID'))
 cmdb=validate_cmdb(json.loads(a.cmdb.read_text()),a.instance_id)
 observation=candidate.validate_tree(a.gitops_root,a.gitops_sha,a.stage,a.mode=='enable-tls')
 owner_sha=run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()
 config={'instance_id':a.instance_id,'mode':a.mode,'environment':a.stage,'namespace':observation['namespace'],'node_name':'dev-k3s-v5','node_uid':a.node_uid,'cluster_uid':a.cluster_uid,'candidate_sha':a.gitops_sha,'platform_sha':a.platform_sha,'owner_sha':owner_sha,'manifest_sha256':observation['manifest_sha256'],'wait_seconds':900}
 if a.mode=='rebuild':
  require(hashlib.sha256(a.prepare_receipt.read_bytes()).hexdigest()==a.prepare_sha256,'prior prepare receipt checksum mismatch')
  prior=json.loads(a.prepare_receipt.read_text())
  config['namespace_uid']=prior.get('namespace_uid')
  require(candidate.SCOPE.UUID.fullmatch(config['namespace_uid'] or ''),'prior SIT namespace UID missing')
  validate_prepare_receipt(prior,config,observation)
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
  require(a.prepare_receipt is not None and re.fullmatch(r'[0-9a-f]{64}',a.prepare_sha256 or ''),'authenticated prepare receipt artifact/digest required')
  require(hashlib.sha256(a.prepare_receipt.read_bytes()).hexdigest()==a.prepare_sha256,'prepare receipt digest mismatch')
  prepare=json.loads(a.prepare_receipt.read_text())
  require(candidate.SCOPE.UUID.fullmatch(prepare.get('namespace_uid','')),'prepare namespace incarnation missing')
  config['namespace_uid']=prepare['namespace_uid']
  require(config['seed_receipt'].get('namespace_uid')==config['namespace_uid'],'seed/prepare namespace incarnation differs')
  config['runtime_mapping']=validate_prepare_receipt(prepare,config,observation)
 a.receipt.unlink(missing_ok=True)
 with dev_binding(cmdb,a.instance_id) as transport:
  d=transport.directory;report=d/'runtime.json'
  variables=d/'vars.json';variables.write_text(json.dumps({'dev_feature_target':'dev-k3s-v5','dev_feature_config':config,'dev_feature_apply':a.mode!='plan','dev_feature_report':str(report)}))
  env={**transport.env,'ANSIBLE_ROLES_PATH':str(ROOT/'roles')}
  if a.mode in ('prepare','rebuild'):
   context={k:config[k] for k in ('candidate_sha','namespace','node_uid','cluster_uid','manifest_sha256')}
   context.update(instance_id=a.instance_id,images=observation['images'],sources=observation['sources'],source={'repository':os.environ.get('GITHUB_REPOSITORY'),'workflow_ref':os.environ.get('GITHUB_WORKFLOW_REF'),'workflow_sha':os.environ.get('GITHUB_SHA'),'owner_sha':owner_sha,'run_id':os.environ.get('GITHUB_RUN_ID'),'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT')})
   env.update(DEV_SYNC_VAULT_TOKEN=transport.token,DEV_REGISTRY_HOST=transport.host,DEV_REGISTRY_KNOWN_HOSTS=str(transport.known),DEV_REGISTRY_PUBLIC_IDENTITY=str(transport.public),DEV_REGISTRY_CONTEXT=json.dumps(context))
  inventory=str(ROOT/'inventory/terraform_cmdb.py')
  play=['ansible-playbook','-i',inventory,str(ROOT/'dev_feature_flux.yml'),'--limit','dev-k3s-v5','-e','@'+str(variables)]
  run([*play,'--syntax-check'],env)
  p=subprocess.run(play,env=env,text=True,capture_output=True,timeout=1200)
  require(report.is_file(),'no runtime owner receipt')
  runtime=json.loads(report.read_text())
  if p.returncode!=0:runtime['result']='owner_failed'
  runtime.update(instance_id=a.instance_id,cmdb_sha256=a.cmdb_sha256,candidate=observation,candidate_control_plane=verified_candidate,candidate_receipt_sha256=a.candidate_sha256,run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
  a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(runtime,sort_keys=True)+'\n')
  require(p.returncode==0 and runtime.get('result')=={'plan':'planned','prepare':'prepared','rebuild':'prepared','submit':'submitted_pending_seed','verify':'verified','enable-tls':'verified'}[a.mode],'namespace deployment did not verify')

def main():
 p=argparse.ArgumentParser()
 for name in ('stage','mode','instance-id','gitops-sha','platform-sha','cmdb-sha256','node-uid','cluster-uid','candidate-sha256'):p.add_argument('--'+name,required=True)
 for name in ('cmdb','gitops-root','receipt','candidate-receipt'):p.add_argument('--'+name,required=True,type=Path)
 p.add_argument('--seed-receipt',type=Path);p.add_argument('--seed-sha256');p.add_argument('--prepare-receipt',type=Path);p.add_argument('--prepare-sha256')
 execute(p.parse_args())
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Dev feature owner rejected scope or execution ('+type(error).__name__+'); raw output suppressed') from None
