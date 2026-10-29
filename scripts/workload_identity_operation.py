#!/usr/bin/env python3
"""Run generic workload identity role against an evidence-bound CMDB target."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'roles/vhosts/vault_jwt_workload_identity/files'))
from vault_contract import IdentitySpec, VaultError


def require(condition):
    if not condition:raise ValueError('workload identity input validation failed')


def build_config(operation,cmdb,receipt,hostkeys,mode):
    require(operation.get('apiVersion')=='operations.svc.plus/v1alpha1' and operation.get('kind')=='VaultWorkloadIdentityOperation')
    spec=operation['spec'];target=spec['target'];vault=spec['vault'];identity=spec['identity']
    require(mode in ('configure','verify') and spec['environment'] in ('sit','uat','prod'))
    require(target['runtime'] in ('k3s','kubernetes'))
    name=target['node'];records=cmdb.get('hosts',cmdb)
    require(isinstance(records,dict) and set(records)=={name})
    host=records[name]
    require(spec['environment'] in host.get('groups',[]))
    require(receipt['result']=='PAUSED_BOOTSTRAP_VERIFIED' and receipt['node']==name
            and receipt['version']==target['kubernetesVersion'] and receipt['gitops_sha']==spec['evidence']['bootstrap']['gitopsSha']
            and receipt['audit']['result']=='AUDIT_ALLOW_DENY_VERIFIED'
            and receipt['common']['result']=='COMMON_NODE_VERIFIED' and receipt['common']['node']==name
            and str(receipt['common']['cmdb_instance_id'])==str(host['instance_id']))
    require(host.get('cloud_provider',host.get('host_vars',{}).get('cloud_provider'))==target['provider'])
    require(host.get('ansible_user')==target['sshUser'])
    require(spec['evidence']['bootstrap']['fileSha256']==hashlib.sha256(receipt['_raw']).hexdigest())
    mapping={'serviceAccount':'service_account','deniedAudience':'denied_audience','probeServiceAccount':'probe_service_account',
             'allowedPath':'allowed_path','deniedPath':'denied_path','ttlSeconds':'ttl_seconds'}
    identity={mapping.get(k,k):v for k,v in identity.items()}
    IdentitySpec(**identity)
    require(Path(hostkeys).is_file() and Path(hostkeys).stat().st_size>0)
    return {'mode':mode,'runtime':target['runtime'],'host':host.get('ip') or host['ansible_host'],
            'ssh_user':target['sshUser'],'known_hosts':str(Path(hostkeys).resolve()),
            'cluster_uid':receipt['cluster_uid'],'node_uid':receipt['node_uid'],'node_name':name,
            'expected_version':target['kubernetesVersion'],'vault_address':vault['address'],
            'admin_policy':vault['adminPolicy'],'identity':identity}


def main():
    p=argparse.ArgumentParser()
    for key in ('gitops','gitops-sha','operation-path','cmdb','cmdb-sha256','bootstrap-receipt','known-hosts','receipt'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--mode',choices=('configure','verify'),required=True);a=p.parse_args()
    root=Path(a.gitops).resolve();require(re.fullmatch('[0-9a-f]{40}',a.gitops_sha))
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSH_AUTH_SOCK') if k in os.environ}
    require(bool(env.get('SSH_AUTH_SOCK')))
    require(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,env=env).strip()==a.gitops_sha)
    require(not subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=all'],text=True,env=env).strip())
    operation=(root/a.operation_path).resolve();require(operation.is_relative_to(root) and operation.is_file())
    cmdb_raw=Path(a.cmdb).read_bytes();require(re.fullmatch('[0-9a-f]{64}',a.cmdb_sha256) and hashlib.sha256(cmdb_raw).hexdigest()==a.cmdb_sha256)
    raw=Path(a.bootstrap_receipt).read_bytes();receipt=json.loads(raw);receipt['_raw']=raw
    config=build_config(yaml.safe_load(operation.read_text()),json.loads(cmdb_raw),receipt,a.known_hosts,a.mode)
    require(os.environ.get('VAULT_ADDR')==config['vault_address'])
    fd=os.open(a.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    run_env={**env,'WORKLOAD_IDENTITY_CONFIG':json.dumps(config),'WORKLOAD_IDENTITY_RESULT':a.receipt,
             'ANSIBLE_ROLES_PATH':str(ROOT/'roles'),'VAULT_TOKEN':os.environ.get('VAULT_TOKEN','')}
    subprocess.run(['ansible-playbook','-i','localhost,',str(ROOT/'vault_jwt_workload_identity.yml')],env=run_env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    result=json.loads(Path(a.receipt).read_text());result.update({'gitops_sha':a.gitops_sha,'bootstrap_sha256':hashlib.sha256(raw).hexdigest(),'operation_path':a.operation_path})
    Path(a.receipt).write_text(json.dumps(result,sort_keys=True)+'\n');print(result['result'])


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError,VaultError,subprocess.SubprocessError):
        raise SystemExit('workload identity owner failed; consult redacted evidence') from None
