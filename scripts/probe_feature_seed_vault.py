#!/usr/bin/env python3
"""Probe only the seed workload identity and path boundaries; no secret output."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://vault.svc.plus/v1/'


def request(url, headers, body=None):
    req = urllib.request.Request(url, headers=headers, data=body, method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(100000)
    except urllib.error.HTTPError as error:
        return error.code, b''


def main():
    url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    separator = '&' if '?' in url else '?'
    status, raw = request(url + separator + urllib.parse.urlencode({'audience': 'vault'}),
                          {'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    if status != 200:
        raise ValueError('OIDC token unavailable')
    status, raw = request(BASE + 'auth/jwt/login', {'Content-Type': 'application/json'},
                          json.dumps({'role':'github-actions-playbooks-feature-seed-sit', 'jwt':json.loads(raw)['value']}).encode())
    if status != 200:
        raise ValueError('seed workload login denied')
    token = json.loads(raw)['auth']['client_token']
    headers = {'X-Vault-Token': token}
    allowed, _ = request(BASE + 'kv/data/CICD/dev/k3s-bootstrap', headers)
    denied, _ = request(BASE + 'kv/data/CICD/dev/ghcr', headers)
    if allowed != 200 or denied != 403:
        raise ValueError('seed path boundaries did not match')
    print('PASS: seed target binding readable and adjacent GHCR path denied')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        raise SystemExit('::error::Seed workload identity probe failed') from None
