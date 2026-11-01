"""Generic Vault JWT workload identity configuration and negative verification."""
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import base64
import json
import hashlib
import re


class VaultError(Exception):
    def __init__(self, message='Vault operation failed', stage='vault', diagnostic=None):
        super().__init__(message)
        self.stage = stage
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class IdentitySpec:
    mount: str
    role: str
    policy: str
    namespace: str
    service_account: str
    audience: str
    denied_audience: str
    probe_service_account: str
    allowed_path: str
    denied_path: str
    ttl_seconds: int = 600

    def __post_init__(self):
        name = r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
        if not all(isinstance(v,str) and re.fullmatch(name,v) and len(v)<=63 for v in
                   (self.mount,self.role,self.policy,self.namespace,self.service_account,self.probe_service_account)):
            raise VaultError('invalid identity name')
        if self.service_account == self.probe_service_account:
            raise VaultError('probe service account must be distinct')
        if (not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}',v)
                    for v in (self.audience,self.denied_audience)) or self.audience == self.denied_audience):
            raise VaultError('invalid audience boundary')
        path = r'[A-Za-z0-9][A-Za-z0-9._/-]*'
        if (not all(isinstance(v,str) and re.fullmatch(path,v) and '..' not in v.split('/')
                    for v in (self.allowed_path,self.denied_path)) or self.allowed_path == self.denied_path):
            raise VaultError('exact distinct Vault paths required')
        if type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 600:
            raise VaultError('token TTL exceeds boundary')

    @property
    def subject(self): return f'system:serviceaccount:{self.namespace}:{self.service_account}'

    @property
    def probe_subject(self): return f'system:serviceaccount:{self.namespace}:{self.probe_service_account}'

    @property
    def policy_hcl(self): return 'path "' + self.allowed_path + '" {\n  capabilities = ["read"]\n}\npath "auth/token/lookup-self" {\n  capabilities = ["read"]\n}\n'

    @property
    def role_config(self):
        return {'role_type':'jwt','user_claim':'sub','bound_subject':self.subject,
                'bound_audiences':[self.audience],'token_no_default_policy':True,
                'token_type':'batch','token_ttl':self.ttl_seconds,'token_max_ttl':self.ttl_seconds,
                'token_explicit_max_ttl':self.ttl_seconds,'token_policies':[self.policy]}


class Client:
    def __init__(self, address, token=None):
        parsed = urlsplit(address)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise VaultError('explicit HTTPS Vault endpoint required')
        self.address, self.token = address.rstrip('/'), token

    def request(self, method, path, body=None, read_json=True):
        headers = {'Accept': 'application/json'}
        if self.token: headers['X-Vault-Token'] = self.token
        data = None if body is None else json.dumps(body).encode('utf-8')
        if data is not None: headers['Content-Type'] = 'application/json'
        req = Request(self.address + '/v1/' + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=15) as response:
                status = response.status
                raw = response.read() if read_json else b''
        except HTTPError as exc:
            return exc.code, None
        except Exception:
            raise VaultError('Vault transport failed') from None
        try:
            return status, json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            raise VaultError('invalid Vault response') from None


def expect(result, codes):
    if result[0] not in codes: raise VaultError('Vault operation returned unexpected status')
    return result[1]


def provision(client, spec, issuer, public_keys):
    """Create missing identity resources, accepting only an exact existing state."""
    if not isinstance(issuer, str) or not issuer or not public_keys:
        raise VaultError('verified issuer and keys required', 'config')
    policy_path = 'sys/policies/acl/' + spec.policy
    mount_path = spec.mount + '/'
    try:
        status, mounts = client.request('GET', 'sys/auth')
    except VaultError:
        raise VaultError('auth mount state could not be read', 'mount') from None
    if status != 200:
        raise VaultError('auth mount state could not be read', 'mount',
                         {'operation': 'auth_mount_list', 'http_status': status})
    existing_mount = mounts.get('data', {}).get(mount_path)
    if existing_mount is not None and existing_mount.get('type') != 'jwt':
        raise VaultError('auth mount exists with an unexpected type', 'mount',
                         {'operation': 'auth_mount_inspect', 'reason': 'unexpected_mount_type'})
    if existing_mount is None:
        try:
            status, capability_result = client.request(
                'POST', 'sys/capabilities-self',
                {'paths': ['sys/auth/' + spec.mount]})
        except VaultError:
            raise VaultError('auth mount capabilities could not be read', 'mount') from None
        if status != 200:
            raise VaultError('auth mount capabilities could not be read', 'mount',
                             {'operation': 'auth_mount_capabilities', 'http_status': status})
        capability_result = capability_result or {}
        capabilities = capability_result.get('capabilities')
        if capabilities is None:
            capabilities = (capability_result.get('data') or {}).get('capabilities')
        if not isinstance(capabilities, list) or any(not isinstance(cap, str) for cap in capabilities):
            raise VaultError('auth mount capabilities response was invalid', 'mount',
                             {'operation': 'auth_mount_capabilities',
                              'reason': 'unexpected_capability_response'})
        missing = sorted({'create', 'update', 'sudo'} - set(capabilities))
        if missing:
            raise VaultError('auth mount capabilities are insufficient', 'mount',
                             {'operation': 'auth_mount_capabilities', 'missing': missing})
    try:
        status, policy = client.request('GET', policy_path)
    except VaultError:
        raise VaultError('reader policy state could not be read', 'policy') from None
    if status == 200:
        if (policy or {}).get('data', {}).get('policy') != spec.policy_hcl:
            raise VaultError('reader policy exists with unreviewed content', 'policy')
        create_policy = False
    elif status == 404:
        create_policy = True
    else:
        raise VaultError('reader policy cannot be safely inspected', 'policy')
    config_path = 'auth/' + spec.mount + '/config'
    expected_config = {'bound_issuer': issuer, 'jwt_validation_pubkeys': public_keys}
    config_missing = False
    try:
        status, current = client.request('GET', config_path)
    except VaultError:
        raise VaultError('dedicated JWT config state could not be read', 'config') from None
    if status == 200:
        current_data = (current or {}).get('data', {})
        if any(current_data.get(key) != value for key, value in expected_config.items()):
            raise VaultError('dedicated JWT config exists with unreviewed content', 'config')
    elif status == 404:
        config_missing = True
    else:
        raise VaultError('dedicated JWT config cannot be safely inspected', 'config')
    role_path = 'auth/' + spec.mount + '/role/' + spec.role
    try:
        status, current = client.request('GET', role_path)
    except VaultError:
        raise VaultError('dedicated JWT role state could not be read', 'role') from None
    role_missing = False
    if status == 200:
        current_data = (current or {}).get('data', {})
        comparable = ('role_type', 'user_claim', 'bound_subject', 'bound_audiences',
                      'token_no_default_policy', 'token_type', 'token_policies')
        if any(current_data.get(key) != spec.role_config[key] for key in comparable):
            raise VaultError('dedicated JWT role exists with unreviewed content', 'role')
        for key in ('token_ttl', 'token_max_ttl', 'token_explicit_max_ttl'):
            if int(current_data.get(key, 0)) != spec.ttl_seconds:
                raise VaultError('dedicated JWT role has an unreviewed TTL', 'role')
    elif status == 404:
        role_missing = True
    else:
        raise VaultError('dedicated JWT role cannot be safely inspected', 'role')
    if create_policy:
        try:
            expect(client.request('PUT', policy_path, {'policy': spec.policy_hcl}), {200, 204})
        except VaultError:
            raise VaultError('reader policy creation failed', 'policy') from None
    if existing_mount is None:
        try:
            status, _ = client.request('POST', 'sys/auth/' + spec.mount, {'type': 'jwt'})
        except VaultError:
            raise VaultError('dedicated JWT mount creation failed', 'mount') from None
        if status not in {200, 204}:
            raise VaultError('dedicated JWT mount creation failed', 'mount',
                             {'operation': 'auth_mount_create', 'http_status': status})
    if config_missing:
        try:
            expect(client.request('POST', config_path, expected_config), {200, 204})
        except VaultError:
            raise VaultError('dedicated JWT config creation failed', 'config') from None
    if role_missing:
        try:
            expect(client.request('POST', role_path, spec.role_config), {200, 204})
        except VaultError:
            raise VaultError('dedicated JWT role creation failed', 'role') from None


def refresh_signing_keys(client, spec, issuer, public_keys, expected_previous_sha256):
    """Replace only an independently reviewed JWT key set after validating existing boundaries."""
    if (not isinstance(expected_previous_sha256, list) or not expected_previous_sha256
            or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in expected_previous_sha256)):
        raise VaultError('reviewed previous key fingerprints required', 'config')
    path = 'auth/' + spec.mount + '/config'
    mounts = expect(client.request('GET', 'sys/auth'), {200}).get('data', {})
    if mounts.get(spec.mount + '/', {}).get('type') != 'jwt':
        raise VaultError('dedicated JWT mount missing or unexpected', 'mount')
    policy = expect(client.request('GET', 'sys/policies/acl/' + spec.policy), {200}).get('data', {})
    if policy.get('policy') != spec.policy_hcl:
        raise VaultError('reader policy differs', 'policy')
    role = expect(client.request('GET', 'auth/' + spec.mount + '/role/' + spec.role), {200}).get('data', {})
    fields = ('role_type', 'user_claim', 'bound_subject', 'bound_audiences', 'token_no_default_policy', 'token_type', 'token_policies')
    if any(role.get(k) != spec.role_config[k] for k in fields) or any(int(role.get(k, 0)) != spec.ttl_seconds for k in ('token_ttl', 'token_max_ttl', 'token_explicit_max_ttl')):
        raise VaultError('reader role differs', 'role')
    current = expect(client.request('GET', path), {200}).get('data', {})
    old = current.get('jwt_validation_pubkeys')
    if current.get('bound_issuer') != issuer or not isinstance(old, list) or not old:
        raise VaultError('issuer or current keys differ', 'config')
    for field in ('default_role', 'jwks_url', 'jwks_ca_pem', 'oidc_client_id', 'oidc_discovery_url', 'oidc_discovery_ca_pem', 'oidc_response_mode'):
        if current.get(field): raise VaultError('unexpected JWT config field', 'config')
    for field in ('jwks_pairs', 'jwt_supported_algs', 'oidc_response_types', 'provider_config', 'unsupported_critical_cert_extensions'):
        if current.get(field): raise VaultError('unexpected JWT config field', 'config')
    if not public_keys or any(not isinstance(k, str) or not k for k in public_keys):
        raise VaultError('verified live public keys required', 'config')
    fingerprint = lambda keys: sorted(hashlib.sha256(key.encode()).hexdigest() for key in keys)
    normalized = lambda keys: [key.strip() for key in keys]
    if normalized(old) == normalized(public_keys):
        return False
    if fingerprint(old) != sorted(expected_previous_sha256):
        raise VaultError('current key fingerprints differ from reviewed values', 'config')
    # Vault JWT config has no compare-and-swap; callers must serialize this mount.
    expect(client.request('POST', path, {'bound_issuer': issuer, 'jwt_validation_pubkeys': public_keys}), {200, 204})
    after = expect(client.request('GET', path), {200}).get('data', {})
    after_keys = after.get('jwt_validation_pubkeys')
    if after.get('bound_issuer') != issuer or not isinstance(after_keys, list) or normalized(after_keys) != normalized(public_keys):
        raise VaultError('JWT config read-back differs', 'config')
    return True


def inspect_signing_keys(client, spec, issuer, public_keys):
    """Compare live Vault and cluster key fingerprints without exposing key material."""
    if not isinstance(issuer, str) or not issuer or not isinstance(public_keys, list) or not public_keys:
        raise VaultError('verified live issuer and keys required', 'config')
    mounts = expect(client.request('GET', 'sys/auth'), {200}).get('data', {})
    if mounts.get(spec.mount + '/', {}).get('type') != 'jwt':
        raise VaultError('dedicated JWT mount missing or unexpected', 'mount')
    policy = expect(client.request('GET', 'sys/policies/acl/' + spec.policy), {200}).get('data', {})
    if policy.get('policy') != spec.policy_hcl:
        raise VaultError('reader policy differs', 'policy')
    role = expect(client.request('GET', 'auth/' + spec.mount + '/role/' + spec.role), {200}).get('data', {})
    fields = ('role_type', 'user_claim', 'bound_subject', 'bound_audiences', 'token_no_default_policy', 'token_type', 'token_policies')
    if any(role.get(k) != spec.role_config[k] for k in fields) or any(
            int(role.get(k, 0)) != spec.ttl_seconds
            for k in ('token_ttl', 'token_max_ttl', 'token_explicit_max_ttl')):
        raise VaultError('reader role differs', 'role')
    current = expect(client.request('GET', 'auth/' + spec.mount + '/config'), {200}).get('data', {})
    old = current.get('jwt_validation_pubkeys')
    if current.get('bound_issuer') != issuer or not isinstance(old, list) or not old:
        raise VaultError('issuer or current keys differ', 'config')
    if any(not isinstance(key, str) or not key for key in public_keys):
        raise VaultError('invalid verified live public keys', 'config')
    digest = lambda keys: sorted(hashlib.sha256(key.encode()).hexdigest() for key in keys)
    return {
        'result': 'VAULT_WORKLOAD_IDENTITY_TRUST_INSPECTED',
        'issuer_matches': True,
        'keys_match': [key.strip() for key in old] == [key.strip() for key in public_keys],
        'current_key_sha256': digest(old),
        'target_key_sha256': digest(public_keys),
    }


def _claims(jwt):
    try:
        parts=jwt.split('.')
        if len(parts)!=3 or not all(parts): raise ValueError()
        return json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    except (ValueError, TypeError):
        raise VaultError('invalid TokenRequest JWT') from None


def verify(client_factory, spec, issuer, good_jwt, wrong_audience_jwt, wrong_service_account_jwt):
    if not all(isinstance(t,str) and t for t in (good_jwt,wrong_audience_jwt,wrong_service_account_jwt)):
        raise VaultError('three workload TokenRequest JWTs required')
    good, wrong_aud, wrong_sa = map(_claims,(good_jwt,wrong_audience_jwt,wrong_service_account_jwt))
    if (good.get('iss')!=issuer or good.get('sub')!=spec.subject or good.get('aud')!=[spec.audience]
            or wrong_aud.get('iss')!=issuer or wrong_aud.get('sub')!=spec.subject or wrong_aud.get('aud')!=[spec.denied_audience]
            or wrong_sa.get('iss')!=issuer or wrong_sa.get('sub')!=spec.probe_subject or wrong_sa.get('aud')!=[spec.audience]):
        raise VaultError('TokenRequest claims differ from acceptance contract')
    unauth=client_factory(None); endpoint='auth/'+spec.mount+'/login'
    result=expect(unauth.request('POST',endpoint,{'role':spec.role,'jwt':good_jwt}),{200})
    auth=result.get('auth',{})
    if set(auth.get('policies',[]))!={spec.policy} or not auth.get('client_token'):
        raise VaultError('unexpected workload token policy')
    scoped=client_factory(auth['client_token'])
    expect(scoped.request('GET',spec.allowed_path,read_json=False),{200})
    expect(scoped.request('GET',spec.denied_path,read_json=False),{403})
    for jwt in (wrong_audience_jwt,wrong_service_account_jwt):
        expect(unauth.request('POST',endpoint,{'role':spec.role,'jwt':jwt},read_json=False),{400,403})
    return {'result':'VAULT_WORKLOAD_IDENTITY_ALLOW_DENY_VERIFIED','allowed_record':True,'adjacent_record_denied':True,
            'wrong_audience_denied':True,'wrong_service_account_denied':True}
