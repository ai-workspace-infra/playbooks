"""Vault operations for the V6 TLS-reader identity.

The caller is responsible for authenticated cluster selection and fetching the
issuer, public keys and Kubernetes TokenRequest JWTs; this module never stores
JWTs, Vault tokens, or secret response bodies. Not a standalone entry point.
"""
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import base64

MOUNT = 'jwt-prod-k3s-v6'
NAME = 'prod-k3s-v6-vault-tls-reader'
AUDIENCE = 'vault-prod-k3s-v6-eso'
SUBJECT = 'system:serviceaccount:vault-prod:vault-tls-reader'
ALLOWED = 'kv/data/CICD/domains/svc.plus'
DENIED = 'kv/data/CICD/domains/v6-authorization-denied-probe'
POLICY = 'path "' + ALLOWED + '" {\n  capabilities = ["read"]\n}\n'
ROLE = {'role_type': 'jwt', 'user_claim': 'sub', 'bound_subject': SUBJECT,
        'bound_audiences': [AUDIENCE], 'token_no_default_policy': True,
        'token_type': 'batch', 'token_ttl': 600, 'token_max_ttl': 600,
        'token_explicit_max_ttl': 600, 'token_policies': [NAME]}


class VaultError(Exception):
    pass


class Client:
    def __init__(self, address, token=None):
        if address != 'https://vault.svc.plus':
            raise VaultError('unexpected Vault endpoint')
        self.address, self.token = address, token

    def request(self, method, path, body=None, read_json=True):
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['X-Vault-Token'] = self.token
        data = None if body is None else json.dumps(body).encode('utf-8')
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(self.address + '/v1/' + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=15) as response:
                status = response.status
                raw = response.read() if read_json else b''
        except HTTPError as exc:
            # Do not return response text: it can contain credentials or server data.
            return exc.code, None
        except Exception:
            raise VaultError('Vault transport failed') from None
        try:
            return status, json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            raise VaultError('invalid Vault response') from None


def expect(result, codes):
    if result[0] not in codes:
        raise VaultError('Vault operation returned unexpected status')
    return result[1]


def provision(client, issuer, public_keys):
    """Create only a new dedicated mount and policy; do not overwrite conflicts.

    All reads and absent checks happen before the first write. A partial failure
    is surfaced for operator review rather than silently retrying or overwriting.
    """
    if not isinstance(issuer, str) or not issuer or not public_keys:
        raise VaultError('verified cluster issuer and public keys required')
    mounts = expect(client.request('GET', 'sys/auth'), {200})
    if MOUNT + '/' in mounts.get('data', {}):
        raise VaultError('dedicated mount already exists; review before changes')
    status, _ = client.request('GET', 'sys/policies/acl/' + NAME)
    if status != 404:
        raise VaultError('dedicated policy exists or is not readable')
    expect(client.request('PUT', 'sys/policies/acl/' + NAME, {'policy': POLICY}), {200, 204})
    expect(client.request('POST', 'sys/auth/' + MOUNT, {'type': 'jwt'}), {200, 204})
    expect(client.request('POST', 'auth/' + MOUNT + '/config',
                          {'bound_issuer': issuer, 'jwt_validation_pubkeys': public_keys}), {200, 204})
    expect(client.request('POST', 'auth/' + MOUNT + '/role/' + NAME, ROLE), {200, 204})


def _claims(jwt):
    try:
        parts = jwt.split('.')
        if len(parts) != 3 or not all(parts):
            raise ValueError()
        return json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        raise VaultError('invalid TokenRequest JWT') from None


def verify(client_factory, issuer, good_jwt, wrong_audience_jwt, wrong_service_account_jwt):
    """Prove actual login and single-record read; tokens and values stay in memory."""
    if not all(isinstance(t, str) and t for t in (good_jwt, wrong_audience_jwt, wrong_service_account_jwt)):
        raise VaultError('three real workload TokenRequest JWTs required')
    good, wrong_aud, wrong_sa = map(_claims, (good_jwt, wrong_audience_jwt, wrong_service_account_jwt))
    if (good.get('iss') != issuer or good.get('sub') != SUBJECT or good.get('aud') != [AUDIENCE]
            or wrong_aud.get('iss') != issuer or wrong_aud.get('sub') != SUBJECT
            or wrong_aud.get('aud') != ['vault-prod-k3s-v6-denied-probe']
            or wrong_sa.get('iss') != issuer
            or wrong_sa.get('sub') != 'system:serviceaccount:vault-prod:default'
            or wrong_sa.get('aud') != [AUDIENCE]):
        raise VaultError('TokenRequest claims differ from acceptance contract')
    unauth = client_factory(None)
    endpoint = 'auth/' + MOUNT + '/login'
    result = expect(unauth.request('POST', endpoint, {'role': NAME, 'jwt': good_jwt}), {200})
    auth = result.get('auth', {})
    if set(auth.get('policies', [])) != {NAME} or not auth.get('client_token'):
        raise VaultError('unexpected workload token policy')
    scoped = client_factory(auth['client_token'])
    # read_json=False ensures secret data is neither parsed nor returned.
    expect(scoped.request('GET', ALLOWED, read_json=False), {200})
    expect(scoped.request('GET', DENIED, read_json=False), {403})
    for jwt in (wrong_audience_jwt, wrong_service_account_jwt):
        expect(unauth.request('POST', endpoint, {'role': NAME, 'jwt': jwt}, read_json=False), {400, 403})
    return {'result': 'VAULT_TLS_IDENTITY_ALLOW_DENY_VERIFIED',
            'allowed_record': True, 'adjacent_record_denied': True,
            'wrong_audience_denied': True, 'wrong_service_account_denied': True}
