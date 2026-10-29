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
    require(mode in ('configure','verify','release-root','release-eso','release-identity')
            and spec['environment'] in ('sit','uat','prod'))
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
    for key in ('gitops','gitops-sha','gitops-tag','operation-path','cmdb','cmdb-sha256','bootstrap-receipt','known-hosts','receipt'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--mode',choices=('configure','verify','release-root','release-eso','release-identity'),required=True);a=p.parse_args()
    root=Path(a.gitops).resolve();require(re.fullmatch('[0-9a-f]{40}',a.gitops_sha))
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSH_AUTH_SOCK') if k in os.environ}
    require(bool(env.get('SSH_AUTH_SOCK')))
    require(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,env=env).strip()==a.gitops_sha)
    require(subprocess.check_output(['git','-C',str(root),'rev-parse','refs/tags/'+a.gitops_tag+'^{commit}'],text=True,env=env).strip()==a.gitops_sha)
    require(not subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=all'],text=True,env=env).strip())
    operation=(root/a.operation_path).resolve();require(operation.is_relative_to(root) and operation.is_file())
    cmdb_raw=Path(a.cmdb).read_bytes();require(re.fullmatch('[0-9a-f]{64}',a.cmdb_sha256) and hashlib.sha256(cmdb_raw).hexdigest()==a.cmdb_sha256)
    raw=Path(a.bootstrap_receipt).read_bytes();receipt=json.loads(raw);receipt['_raw']=raw
    config=build_config(yaml.safe_load(operation.read_text()),json.loads(cmdb_raw),receipt,a.known_hosts,a.mode)
    config.update({'gitops_checkout':str(root),'gitops_sha':a.gitops_sha,'gitops_tag':a.gitops_tag,
                   'release_stage':a.mode.removeprefix('release-') if a.mode.startswith('release-') else ''})
    require(os.environ.get('VAULT_ADDR')==config['vault_address'])
    fd=os.open(a.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    failure_path=a.receipt+'.failure'
    run_env={**env,'WORKLOAD_IDENTITY_CONFIG':json.dumps(config),'WORKLOAD_IDENTITY_RESULT':a.receipt,
             'WORKLOAD_IDENTITY_FAILURE':failure_path,
             'ANSIBLE_ROLES_PATH':str(ROOT/'roles'),'VAULT_TOKEN':os.environ.get('VAULT_TOKEN','')}
    result_run=subprocess.run(['ansible-playbook','-i','localhost,',str(ROOT/'vault_jwt_workload_identity.yml')],
                              env=run_env,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if result_run.returncode:
        stage='owner_execution'
        try:
            failure=json.loads(Path(failure_path).read_text())
            allowed={'cluster_identity','cluster_namespace_read','cluster_nodes_read','cluster_node_receipt_match',
                     'cluster_service_accounts_read','cluster_oidc_discovery','cluster_token_requests',
                     'cluster_identity_mismatch','administrator_scope','workload_login_and_path_checks',
                     'vault_provision','vault_policy','vault_mount','vault_config','vault_role',
                     'release_cluster_identity','release_namespace_read','release_nodes_read',
                     'release_node_receipt_match','release_declaration','release_live_graph_read',
                     'release_live_graph_mismatch','release_live_gate_order','release_config_source_ready',
                     'release_root_ready','release_external-secrets_ready','release_identity_service_accounts',
                     'release_source_revision','release_root_revision','release_live_gate_mismatch',
                     'release_vault_config_source_ready','release_identity_ready',
                     'release_source_apply','release_root_apply','release_revision_validation',
                     'release_post_reconcile_graph',
                     'input_validation'}
            if failure.get('result')=='FAILED' and failure.get('stage') in allowed:
                stage=failure['stage']
        except (OSError,ValueError,TypeError):
            pass
        Path(a.receipt).write_text(json.dumps({'result':'FAILED','stage':stage},sort_keys=True)+'\n')
        raise RuntimeError('workload identity owner failed at stage='+stage)
    result=json.loads(Path(a.receipt).read_text());result.update({'gitops_sha':a.gitops_sha,'bootstrap_sha256':hashlib.sha256(raw).hexdigest(),'operation_path':a.operation_path})
    Path(a.receipt).write_text(json.dumps(result,sort_keys=True)+'\n');print(result['result'])


if __name__=='__main__':
    try:main()
    except RuntimeError as exc:
        message=str(exc)
        stages={'owner_execution','cluster_identity','cluster_namespace_read','cluster_nodes_read','cluster_node_receipt_match',
                'cluster_service_accounts_read','cluster_oidc_discovery','cluster_token_requests','cluster_identity_mismatch',
                'administrator_scope','workload_login_and_path_checks','vault_provision','vault_policy',
                'vault_mount','vault_config','vault_role','release_cluster_identity',
                'release_namespace_read','release_nodes_read','release_node_receipt_match','release_declaration',
                'release_live_graph_read','release_live_graph_mismatch','release_live_gate_order',
                'release_config_source_ready','release_root_ready','release_external-secrets_ready',
                'release_identity_service_accounts','release_source_revision','release_root_revision',
                'release_live_gate_mismatch','release_vault_config_source_ready','release_identity_ready',
                'release_source_apply','release_root_apply','release_revision_validation',
                'release_post_reconcile_graph',
                'input_validation'}
        stage=message.rsplit('stage=',1)[-1]
        if stage not in stages: stage='input_validation'
        print('workload_identity_owner_failure stage='+stage,file=sys.stderr)
        raise SystemExit('workload identity owner failed; consult redacted evidence') from None
    except (OSError,ValueError,KeyError,TypeError,VaultError,subprocess.SubprocessError):
        print('workload_identity_owner_failure stage=input_validation',file=sys.stderr)
        raise SystemExit('workload identity owner failed; consult redacted evidence') from None
