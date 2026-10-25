#!/usr/bin/env python3
"""Reconcile an explicitly selected GitHub OIDC contract; never print credentials."""
import json
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def validate(config):
    if not config.get('address', '').startswith('https://') or not config.get('token'):
        raise ValueError('explicit HTTPS address and runtime identity required')
    roles, policies = config.get('roles', {}), config.get('policies', {})
    if not roles or len(roles) > 40 or not policies:
        raise ValueError('bounded role and policy selection required')
    if any(not re.fullmatch(r'github-actions-[a-z0-9-]+', p) for p in policies):
        raise ValueError('policy names must belong to the GitHub Actions contract')
    for name, role in roles.items():
        if not re.fullmatch(r'github-actions-[a-z0-9-]+-(sit|uat|prod)', name):
            raise ValueError('only explicitly selected ordinary environment roles are supported')
        claims = role.get('bound_claims', {})
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', claims.get('repository', '')) or not claims.get('job_workflow_ref') or not claims.get('ref'):
            raise ValueError('repository, exact workflow allowlist and ref bindings are required')
        workflows = claims['job_workflow_ref']
        workflows = workflows if isinstance(workflows, list) else [workflows]
        if any('/.github/workflows/' not in w or '*' in w.split('@')[0] for w in workflows):
            raise ValueError('wildcard workflow names are forbidden')
        if role.get('user_claim') != 'sub' or role.get('role_type') != 'jwt' or role.get('bound_audiences') != ['vault'] or role.get('token_no_default_policy') is not True:
            raise ValueError('JWT audience and no-default-policy contract required')
        if role.get('token_type') != 'batch' or role.get('token_ttl') != '1h' or role.get('token_max_ttl') != '1h':
            raise ValueError('short-lived batch identity required')
        refs = claims['ref'] if isinstance(claims['ref'], list) else [claims['ref']]
        if name.endswith('-prod') and any(r not in ('refs/tags/v*', 'refs/heads/release/v*') for r in refs):
            raise ValueError('production must use stable release tags or protected release lines')
        if not role.get('token_policies') or any(p not in policies for p in role['token_policies']):
            raise ValueError('all referenced policies must be supplied explicitly')
    if set(policies) != {p for r in roles.values() for p in r['token_policies']}:
        raise ValueError('unreferenced policy mutation is forbidden')


def equivalent(actual, desired):
    def norm(v):
        if v == '1h':
            return 3600
        if isinstance(v, list):
            return sorted(v)
        return v
    return all(norm(actual.get(k)) == norm(v) for k, v in desired.items())


def reconcile(config):
    validate(config)
    base = config['address'].rstrip('/') + '/v1/'
    def request(path, payload=None):
        req = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                      headers={'X-Vault-Token': config['token'], 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=20) as res:
                body = res.read()
                return json.loads(body).get('data', {}) if body else {}
        except HTTPError as exc:
            if exc.code == 404 and payload is None:
                return None
            raise RuntimeError('Vault operation failed with HTTP ' + str(exc.code)) from None
    if 'jwt/' not in request('sys/auth'):
        raise ValueError('JWT mount must be established before role reconciliation')
    desired_config = {'oidc_discovery_url': 'https://token.actions.githubusercontent.com',
                      'bound_issuer': 'https://token.actions.githubusercontent.com'}
    pending = []
    if not equivalent(request('auth/jwt/config') or {}, desired_config):
        pending.append(('auth/jwt/config', desired_config))
    for name, policy in config['policies'].items():
        if (request('sys/policies/acl/' + name) or {}).get('policy', '').strip() != policy.strip():
            pending.append(('sys/policies/acl/' + name, {'policy': policy}))
    for name, role in config['roles'].items():
        desired = {k: v for k, v in role.items() if k not in ('description', 'role_name')}
        if not equivalent(request('auth/jwt/role/' + name) or {}, desired):
            pending.append(('auth/jwt/role/' + name, desired))
    for path, desired in pending:
        if config.get('apply'):
            request(path, desired)
    return {'status': 'applied' if config.get('apply') else 'preview', 'roles': len(config['roles']),
            'policies': len(config['policies']), 'changed': len(pending),
            'issuer': desired_config['bound_issuer']}


if __name__ == '__main__':
    try:
        print(json.dumps(reconcile(json.load(sys.stdin))))
    except Exception as error:
        print('OIDC reconciliation stopped: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
