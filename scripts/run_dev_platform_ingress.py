#!/usr/bin/env python3
"""Strict Dev shared ingress adapter; inherits the existing authenticated SSH binding."""
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path
from dev_k3s_bootstrap import validate_cmdb
from dev_ssh_binding import binding,run
from run_dev_feature_flux import validate_candidate_receipt
import dev_platform_candidate as candidate
ROOT=Path(__file__).resolve().parents[1]

def execute(a):
 S=candidate.S
 S.require(a.mode in ('plan','reconcile','verify'),'platform mode required')
 for path,digest in ((a.cmdb,a.cmdb_sha256),(a.candidate_receipt,a.candidate_sha256)):
  S.require(re.fullmatch(r'[a-f0-9]{64}',digest) and hashlib.sha256(path.read_bytes()).hexdigest()==digest,'authenticated artifact checksum differs')
 verified=validate_candidate_receipt(json.loads(a.candidate_receipt.read_text()),a.gitops_sha,'platform-ingress',os.environ.get('GITHUB_RUN_ID'))
 cmdb=validate_cmdb(json.loads(a.cmdb.read_text()),a.instance_id)
 observation=candidate.validate_tree(a.gitops_root,a.gitops_sha)
 owner_sha=run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()
 config={'mode':a.mode,'environment':'dev','namespace':S.NS,'node_name':'dev-k3s-v5','node_uid':a.node_uid,'cluster_uid':a.cluster_uid,'candidate_sha':a.gitops_sha,'platform_sha':a.platform_sha,'owner_sha':owner_sha,'manifest_sha256':observation['manifest_sha256'],'wait_seconds':900}
 S.validate(config);a.receipt.unlink(missing_ok=True)
 with binding(cmdb,a.instance_id) as transport:
  report=transport.directory/'runtime.json';variables=transport.directory/'vars.json'
  variables.write_text(json.dumps({'dev_platform_target':'dev-k3s-v5','dev_platform_config':config,'dev_platform_report':str(report)}))
  env={**transport.env,'ANSIBLE_ROLES_PATH':str(ROOT/'roles')}
  play=['ansible-playbook','-i',str(ROOT/'inventory/terraform_cmdb.py'),str(ROOT/'dev_platform_ingress.yml'),'--limit','dev-k3s-v5','-e','@'+str(variables)]
  run([*play,'--syntax-check'],env)
  result=subprocess.run(play,env=env,text=True,capture_output=True,timeout=1200)
  S.require(report.is_file(),'no platform owner runtime receipt')
  runtime=json.loads(report.read_text());runtime.update(instance_id=a.instance_id,cmdb_sha256=a.cmdb_sha256,candidate=observation,candidate_control_plane=verified,candidate_receipt_sha256=a.candidate_sha256,run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
  a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(runtime,sort_keys=True)+'\n')
  S.require(result.returncode==0 and runtime.get('result')==('planned' if a.mode=='plan' else 'verified'),'platform reconciliation not verified')

def main():
 p=argparse.ArgumentParser()
 for key in ('mode','instance-id','gitops-sha','platform-sha','cmdb-sha256','node-uid','cluster-uid','candidate-sha256'):p.add_argument('--'+key,required=True)
 for key in ('cmdb','gitops-root','receipt','candidate-receipt'):p.add_argument('--'+key,type=Path,required=True)
 execute(p.parse_args())
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Dev platform owner blocked ('+type(error).__name__+'); raw output suppressed') from None
