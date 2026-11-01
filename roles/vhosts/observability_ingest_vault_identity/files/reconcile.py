#!/usr/bin/env python3
"""Reconcile one reviewed Vault Kubernetes identity without overwriting drift."""
import json
import os
import re
import sys
import urllib.error
import urllib.request


def request(address, token, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(address + '/v1/' + path, data=data, method=method,
                                 headers={'X-Vault-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        if method == 'GET' and exc.code == 404:
            return None
        raise ValueError(f'Vault request failed: {method} {path} status {exc.code}') from None


def main():
    req = json.load(sys.stdin)
    address = req['address'].rstrip('/')
    token = req['token']
    mount = req['mount']
    policy = req['policy']
    role = req['role']
    apply = req.get('apply', False)
    name = 'observability-ingest-reader'
    if not address.startswith('https://') or not token or not re.fullmatch(r'[a-zA-Z0-9_-]+', mount):
        raise ValueError('Invalid Vault address, token, or auth mount')
    if not isinstance(apply, bool):
        raise ValueError('apply must be boolean')
    if (policy.get('apiVersion') != 'security.gitops/v1alpha1' or
        role.get('apiVersion') != 'security.gitops/v1alpha1' or
        policy.get('kind') != 'VaultACLPolicy' or role.get('kind') != 'VaultKubernetesAuthRole'):
        raise ValueError('Unexpected declaration kind')
    if policy['metadata']['name'] != name or role['metadata']['name'] != name:
        raise ValueError('Unexpected identity name')
    paths = policy['spec']['paths']
    if paths != [{'path': 'kv/data/CICD/observability', 'capabilities': ['read']},
                 {'path': 'auth/token/renew-self', 'capabilities': ['update']},
                 {'path': 'auth/token/lookup-self', 'capabilities': ['read']}]:
        raise ValueError('Policy differs from approved exact scope')
    expected = {'bound_service_account_names': ['observability-ingest-vault-reader'],
                'bound_service_account_namespaces': ['web-saas-k3s-v5'],
                'audience': 'vault-web-saas-k3s-v5-prod', 'token_policies': [name],
                'token_no_default_policy': True, 'token_type': 'service',
                'token_ttl': '5m', 'token_max_ttl': '15m'}
    if role['spec'] != expected:
        raise ValueError('Role differs from reviewed identity contract')
    hcl = '\n\n'.join('path '+json.dumps(p['path'])+' {\n  capabilities = '+json.dumps(p['capabilities'])+'\n}' for p in paths)+'\n'
    mounts = request(address, token, 'GET', 'sys/auth')
    if not mounts or mounts['data'].get(mount+'/', {}).get('type') != 'kubernetes':
        raise ValueError('Reviewed Kubernetes auth mount is absent or has a different type')
    policy_path = 'sys/policies/acl/'+name
    role_path = 'auth/'+mount+'/role/'+name
    current_policy = request(address, token, 'GET', policy_path)
    current_role = request(address, token, 'GET', role_path)
    compact = lambda s: re.sub(r'\s+', '', s)
    if current_policy is not None and compact(current_policy['data']['policy']) != compact(hcl):
        raise ValueError('Existing policy differs; refusing overwrite')
    def check_role(value):
        if value is None: return
        actual = value['data']
        seconds = {'token_ttl': 300, 'token_max_ttl': 900}
        for key, desired in expected.items():
            if actual.get(key) != seconds.get(key, desired):
                raise ValueError('Existing role differs in '+key)
        if actual.get('bound_service_account_namespace_selector'):
            raise ValueError('Existing role has an unexpected namespace selector')
    check_role(current_role)
    if not apply:
        if current_policy is None or current_role is None:
            raise ValueError('Policy or role missing')
        print(json.dumps({'status':'matched','changed':0})); return
    changed=0
    if current_policy is None:
        request(address, token, 'PUT', policy_path, {'policy': hcl}); changed+=1
    if current_role is None:
        request(address, token, 'POST', role_path, expected); changed+=1
    verified_policy=request(address, token, 'GET', policy_path)
    verified_role=request(address, token, 'GET', role_path)
    if not verified_policy or compact(verified_policy['data']['policy']) != compact(hcl) or not verified_role:
        raise ValueError('Vault readback failed')
    check_role(verified_role)
    print(json.dumps({'status':'matched','changed':changed}))


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, OSError, urllib.error.URLError):
        print(json.dumps({'status':'failed','message':'Vault identity reconcile failed; inspect scope, existing state, and access'}), file=sys.stderr)
        raise SystemExit(1)
