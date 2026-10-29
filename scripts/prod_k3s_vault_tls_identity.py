#!/usr/bin/env python3
"""Controlled V6 TLS-reader identity owner. The caller supplies verified evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml
import prod_k3s_bootstrap as bootstrap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'roles/vhosts/vault_jwt_workload_identity/files'))
sys.path.insert(0, str(ROOT / 'scripts/v6_vault_tls_identity'))
import cluster_identity
import identity_execution
import remote_kubectl
import vault_contract


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def prepare_inputs(cmdb_raw, cmdb_sha, declaration, gitops_sha, checkout, bootstrap_raw, bootstrap_sha256, bootstrap_gitops_sha):
    require(re.fullmatch(r'[0-9a-f]{64}', cmdb_sha or '') is not None
            and hashlib.sha256(cmdb_raw).hexdigest() == cmdb_sha, 'CMDB digest differs')
    receipt = identity_execution.receipt_contract(bootstrap_raw, bootstrap_sha256, bootstrap_gitops_sha)
    name, cmdb = bootstrap.prepare(json.loads(cmdb_raw), declaration, gitops_sha, checkout, Path('/dev/null'), phase='common')
    require(str(cmdb['hosts'][name]['instance_id']) == str(receipt['common']['cmdb_instance_id']),
            'CMDB instance differs from bootstrap receipt')
    return cmdb['hosts'][name]['ip']


def main():
    p = argparse.ArgumentParser()
    for field in ('cmdb', 'cmdb-sha256', 'gitops', 'gitops-sha', 'bootstrap-receipt',
                  'bootstrap-sha256', 'bootstrap-gitops-sha', 'known-hosts', 'receipt', 'operation-path'):
        p.add_argument('--' + field, required=True)
    p.add_argument('--mode', required=True, choices=('configure', 'verify'))
    a = p.parse_args()
    checkout = Path(a.gitops).resolve()
    require(re.fullmatch(r'[0-9a-f]{40}', a.gitops_sha or '') is not None, 'fixed GitOps commit required')
    # Never let Git subprocesses inherit credentials.
    child_env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSH_AUTH_SOCK') if k in os.environ}
    actual = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True, env=child_env).strip()
    dirty = subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=all'], text=True, env=child_env).strip()
    require(actual == a.gitops_sha and not dirty, 'reviewed GitOps checkout mismatch')
    bootstrap_raw = Path(a.bootstrap_receipt).read_bytes()
    cmdb_raw = Path(a.cmdb).read_bytes()
    operation_file = (checkout / a.operation_path).resolve()
    require(operation_file.is_relative_to(checkout) and operation_file.is_file(), 'identity operation must reside in reviewed checkout')
    operation = yaml.safe_load(operation_file.read_text())
    require(operation.get('apiVersion') == 'operations.svc.plus/v1alpha1' and operation.get('kind') == 'VaultWorkloadIdentityOperation', 'invalid identity operation')
    op = operation['spec']
    require(op['environment'] == 'prod' and op['target']['node'] == identity_execution.NODE
            and op['target']['declaration'] == bootstrap.RESOURCE, 'identity operation target mismatch')
    require(op['evidence']['bootstrap']['gitopsSha'] == a.bootstrap_gitops_sha
            and op['evidence']['bootstrap']['fileSha256'] == a.bootstrap_sha256, 'identity operation evidence mismatch')
    declaration = yaml.safe_load((checkout / op['target']['declaration']).read_text())
    host = prepare_inputs(cmdb_raw, a.cmdb_sha256, declaration, a.gitops_sha, checkout,
                          bootstrap_raw, a.bootstrap_sha256, a.bootstrap_gitops_sha)
    require(bool(child_env.get('SSH_AUTH_SOCK')), 'isolated SSH agent required')
    require(os.environ.get('VAULT_ADDR') == op['vault']['address'], 'Vault endpoint differs from reviewed operation')
    bootstrap_evidence = identity_execution.receipt_contract(bootstrap_raw, a.bootstrap_sha256, a.bootstrap_gitops_sha)
    require(bootstrap_evidence['version'] == op['target']['kubernetesVersion'], 'Kubernetes version differs from reviewed operation')
    # Reserve an exclusive private receipt before any Vault write. An empty
    # receipt after failure never constitutes success evidence.
    fd = os.open(a.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    mapping = {'serviceAccount': 'service_account', 'deniedAudience': 'denied_audience',
               'probeServiceAccount': 'probe_service_account', 'allowedPath': 'allowed_path',
               'deniedPath': 'denied_path', 'ttlSeconds': 'ttl_seconds'}
    identity = {mapping.get(k, k): v for k, v in op['identity'].items()}
    vault_contract.IdentitySpec(**identity)
    config = {'mode': a.mode, 'runtime': op['target']['runtime'], 'host': host, 'ssh_user': op['target']['sshUser'],
              'known_hosts': a.known_hosts, 'cluster_uid': bootstrap_evidence['cluster_uid'],
              'node_uid': bootstrap_evidence['node_uid'], 'node_name': identity_execution.NODE,
              'expected_version': op['target']['kubernetesVersion'], 'vault_address': op['vault']['address'],
              'admin_policy': op['vault']['adminPolicy'], 'identity': identity}
    run_env = {**child_env, 'WORKLOAD_IDENTITY_CONFIG': json.dumps(config),
               'WORKLOAD_IDENTITY_RESULT': a.receipt, 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles'),
               'VAULT_TOKEN': os.environ.get('VAULT_TOKEN', '')}
    subprocess.run(['ansible-playbook', '-i', 'localhost,', str(ROOT / 'vault_jwt_workload_identity.yml')],
                   env=run_env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = json.loads(Path(a.receipt).read_text())
    result['bootstrap_gitops_sha'] = a.bootstrap_gitops_sha
    result['bootstrap_receipt_sha256'] = a.bootstrap_sha256
    result['gitops_sha'] = a.gitops_sha
    with Path(a.receipt).open('w', encoding='utf-8') as stream:
        json.dump(result, stream, sort_keys=True)
        stream.write('\n')
    print(result['result'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            identity_execution.IdentityError, cluster_identity.ClusterError, vault_contract.VaultError):
        raise SystemExit('V6 TLS identity operation failed; inspect redacted control-plane evidence') from None
