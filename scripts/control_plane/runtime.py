#!/usr/bin/env python3
"""Resolve reference-only policy in task memory; never write controller secrets."""
import json
import os
import subprocess
import sys
from urllib.parse import quote

from entrypoint import SECRET_KEYS, Refused, require, validate_runtime_policy


def resolve(policy, environment, target=None):
    validate_runtime_policy(policy, environment)
    require(bool(os.environ.get(policy['vault_token_env'])), 'Short-lived Vault identity is absent')
    vault_environment = {key: value for key, value in os.environ.items() if key != 'VAULT_AGENT_ADDR'}
    vault_environment.update(VAULT_ADDR=policy['vault_addr'], VAULT_TOKEN=os.environ[policy['vault_token_env']])
    records, result = {}, {}
    for key, reference in policy['secret_refs'].items():
        path, field = reference.split('#')
        if path not in records:
            response = subprocess.run(['vault', 'kv', 'get', '-format=json', path],
                env=vault_environment, text=True, capture_output=True, timeout=30)
            require(response.returncode == 0, 'Required Vault record is unavailable')
            records[path] = json.loads(response.stdout)['data']['data']
        value = records[path].get(field)
        require(isinstance(value, str) and value and not any(c in value for c in ('\n', '\r', '\x00', '{{', '{%', '{#')),
                'Required Vault field is absent or unsafe for native templates')
        result[key] = value
    secrets = {key: result.pop(key) for key in SECRET_KEYS}
    secrets.update(POSTGRES_USER='postgres', POSTGRES_DB='postgres', ACCOUNT_PG_USER='account_user',
        DATABASE_RUNTIME_ROLE='primary', DATABASE_BACKGROUND_WRITERS='false',
        DATABASE_URL='postgresql://account_user:' + quote(secrets['ACCOUNT_PG_PASSWORD'], safe='') + '@stunnel-client:15432/account?sslmode=disable',
        SMTP_HOST='127.0.0.1', SMTP_PORT='1025', SMTP_FROM='candidate@invalid.example', SMTP_TLS_MODE='none',
        SMTP_USERNAME='', SMTP_PASSWORD='', GITHUB_CLIENT_ID='', GITHUB_CLIENT_SECRET='', GOOGLE_CLIENT_ID='',
        GOOGLE_CLIENT_SECRET='', STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='', ROOT_BOOTSTRAP_EMAIL='', ROOT_BOOTSTRAP_PASSWORD='',
        BILLING_INGEST_MODE='push', ACCOUNT_SERVICE_URL='http://accounts:8080', SERVER_SERVICE_URL='http://accounts:8080/api')
    if target:
        secrets.update(NEXT_PUBLIC_ACCOUNT_SERVICE_URL='https://' + target['accounts_domain'],
            NEXT_PUBLIC_SERVER_SERVICE_URL='https://' + target['console_domain'] + '/api',
            XWORKMATE_SHARED_TENANT_DOMAIN=target['domain_base'], XWORKMATE_SHARED_TENANT_DOMAINS=target['domain_base'])
    return {**result, 'secrets': secrets}


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        print(json.dumps(resolve(request['policy'], request['environment'], request.get('target'))))
    except Exception:
        print('Runtime credential resolution failed; values withheld.', file=sys.stderr)
        sys.exit(1)
