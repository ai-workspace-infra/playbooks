#!/usr/bin/env python3
"""One-time system-admin setup of reviewed V6 identity; never a CI fallback."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import yaml

NAMES = tuple('github-actions-platform-ops-toolkit-prod-k3s-v6-' + kind for kind in ('iac', 'bootstrap'))
SSH_PATH = 'kv/prod/platform/prod-k3s-v6/ssh'


def vault(args, data=None, missing=False):
    result = subprocess.run(['vault', *args], input=data, capture_output=True, text=True)
    if result.returncode:
        if missing and any(text in result.stderr for text in ('No value found', 'No policy named')):
            return None
        raise ValueError('Vault operation failed: ' + args[0])
    return result.stdout


def public_key(private):
    result = subprocess.run(['ssh-keygen', '-y', '-f', '/dev/stdin'], input=private,
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Unable to validate SSH key identity')
    return ' '.join(result.stdout.split()[:2])


def main():
    p = argparse.ArgumentParser()
    for key in ('orchestrator', 'orchestrator-sha', 'gitops', 'gitops-sha'):
        p.add_argument('--' + key, required=True)
    a = p.parse_args()
    for path, sha in [(a.orchestrator, a.orchestrator_sha), (a.gitops, a.gitops_sha)]:
        if subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip() != sha:
            raise ValueError('Reviewed source SHA mismatch')
        if subprocess.check_output(['git', '-C', path, 'status', '--porcelain'], text=True).strip():
            raise ValueError('Dirty identity configuration checkout')
    if os.environ.get('VAULT_ADDR') != 'https://vault.svc.plus':
        raise ValueError('Explicit production Vault endpoint required')
    src = Path(a.orchestrator) / 'scripts/vault'
    # Both payloads are validated before any write. Existing identities may only
    # be identical; this initializer never widens another role or overwrites keys.
    plans = []
    for name in NAMES:
        role = json.loads((src / 'roles' / (name + '.json')).read_text())
        policy = (src / 'policies' / (name + '.hcl')).read_text()
        if role['token_policies'] != [name] or role['token_type'] != 'batch' or role['token_explicit_max_ttl'] > 1200:
            raise ValueError('Unsafe role token boundary')
        claims = role['bound_claims']
        if claims['repository'] != 'ai-workspace-infra/Platform-Orchestrator' or claims['environment'] != 'prod' or claims['ref'] != ['refs/tags/v6.*'] or claims.get('ref_protected') != 'true':
            raise ValueError('Unsafe workload binding')
        previous = vault(['read', '-format=json', 'auth/jwt/role/' + name], missing=True)
        if previous:
            values = json.loads(previous)['data']
            if any(values.get(k) != v for k, v in role.items()):
                raise ValueError('Existing V6 role differs; explicit identity review required')
        existing_policy = vault(['policy', 'read', name], missing=True)
        if existing_policy and existing_policy.strip() != policy.strip():
            raise ValueError('Existing V6 policy differs')
        plans.append((name, role, policy, previous, existing_policy))
    declaration = yaml.safe_load((Path(a.gitops) / 'resources/svc.plus/prod/akamai/prod-k3s-v6.yaml').read_text())
    allowed = {' '.join(k['public'].split()[:2]) for k in declaration['ssh_keys']}
    old = vault(['kv', 'get', '-format=json', SSH_PATH], missing=True)
    if old:
        key = json.loads(old)['data']['data']['SSH_PRIVATE_KEY']
    else:
        source = json.loads(vault(['kv', 'get', '-format=json', 'kv/CICD/prod']))['data']['data']
        key = base64.b64decode(source['SSH_PRIVATE_DEPLOY_KEY_B64'], validate=True).decode()
    if public_key(key) not in allowed:
        raise ValueError('Runtime SSH key does not match declared authorized public keys')
    for name, role, policy, previous, existing_policy in plans:
        if not existing_policy:
            vault(['policy', 'write', name, '-'], policy)
        if not previous:
            vault(['write', 'auth/jwt/role/' + name, '-'], json.dumps(role))
    if not old:
        vault(['kv', 'put', '-cas=0', SSH_PATH, '-'], json.dumps({'SSH_PRIVATE_KEY': key}))
    print(json.dumps({'result': 'IDENTITY_CONFIGURED', 'roles': list(NAMES), 'ssh_path': SSH_PATH,
                      'gitops_sha': a.gitops_sha, 'orchestrator_sha': a.orchestrator_sha}))


if __name__ == '__main__':
    main()
