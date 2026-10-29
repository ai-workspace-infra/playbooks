#!/usr/bin/env python3
"""Controlled V6 TLS-reader identity owner. The caller supplies verified evidence."""
import argparse
import functools
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
sys.path.insert(0, str(ROOT / 'roles/vhosts/vault_v6_tls_identity/files'))
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


def client_factory(admin_token):
    def make(admin, token=None):
        if admin:
            require(bool(admin_token), 'short-lived scoped Vault identity required')
            client = vault_contract.Client('https://vault.svc.plus', admin_token)
            data = vault_contract.expect(client.request('GET', 'auth/token/lookup-self'), {200})['data']
            policies = set(data.get('policies') or [])
            require(policies == {'github-actions-platform-ops-toolkit-prod-k3s-v6-vault-tls-admin'}
                    and data.get('ttl', 0) > 0 and data.get('ttl', 0) <= 1200,
                    'Vault administrator identity must be scoped and short-lived')
            return client
        return vault_contract.Client('https://vault.svc.plus', token)
    return make


def main():
    p = argparse.ArgumentParser()
    for field in ('cmdb', 'cmdb-sha256', 'gitops', 'gitops-sha', 'bootstrap-receipt',
                  'bootstrap-sha256', 'bootstrap-gitops-sha', 'known-hosts', 'receipt'):
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
    declaration = yaml.safe_load((checkout / bootstrap.RESOURCE).read_text())
    host = prepare_inputs(cmdb_raw, a.cmdb_sha256, declaration, a.gitops_sha, checkout,
                          bootstrap_raw, a.bootstrap_sha256, a.bootstrap_gitops_sha)
    require(bool(child_env.get('SSH_AUTH_SOCK')), 'isolated SSH agent required')
    require(os.environ.get('VAULT_ADDR') == 'https://vault.svc.plus', 'explicit production Vault endpoint required')
    ssh = remote_kubectl.RemoteKubectl(host, a.known_hosts, env=child_env)
    collector = functools.partial(cluster_identity.collect, run=ssh)
    # Reserve an exclusive private receipt before any Vault write. An empty
    # receipt after failure never constitutes success evidence.
    fd = os.open(a.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        result = identity_execution.execute(a.mode, bootstrap_raw, a.bootstrap_sha256, a.bootstrap_gitops_sha,
                                            client_factory(os.environ.get('VAULT_TOKEN')), collector)
        # No tokens, credentials, signing keys or secret contents are included.
        result['gitops_sha'] = a.gitops_sha
        json.dump(result, stream, sort_keys=True)
        stream.write('\n')
    print(result['result'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            identity_execution.IdentityError, cluster_identity.ClusterError, vault_contract.VaultError):
        raise SystemExit('V6 TLS identity operation failed; inspect redacted control-plane evidence') from None
