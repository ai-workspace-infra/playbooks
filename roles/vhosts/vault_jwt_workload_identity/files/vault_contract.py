"""Generic Vault JWT workload identity configuration and negative verification."""
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import base64
import json
import re


class VaultError(Exception):
    def __init__(self, message='Vault operation failed', stage='vault'):
        super().__init__(message)
        self.stage = stage


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
    def policy_hcl(self): return 'path "' + self.allowed_path + '" {\n  capabilities = ["read"]\n}\n'

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
    """Create an absent mount and exact policy, rejecting ambiguous partial state."""
    if not isinstance(issuer, str) or not issuer or not public_keys:
        raise VaultError('verified issuer and keys required', 'config')
    policy_path = 'sys/policies/acl/' + spec.policy
    mount_path = spec.mount + '/'
    try:
        mounts = expect(client.request('GET', 'sys/auth'), {200})
    except VaultError as exc:
        raise VaultError('auth mount state could not be read', 'mount') from None
    if mount_path in mounts.get('data', {}):
        raise VaultError('auth mount already exists; do not overwrite unverified state', 'mount')
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
    if create_policy:
        try:
            expect(client.request('PUT', policy_path, {'policy': spec.policy_hcl}), {200, 204})
        except VaultError:
            raise VaultError('reader policy creation failed', 'policy') from None
    try:
        expect(client.request('POST', 'sys/auth/' + spec.mount, {'type': 'jwt'}), {200, 204})
    except VaultError:
        raise VaultError('dedicated JWT mount creation failed', 'mount') from None
    try:
        expect(client.request('POST', 'auth/' + spec.mount + '/config',
                              {'bound_issuer': issuer, 'jwt_validation_pubkeys': public_keys}), {200, 204})
    except VaultError:
        raise VaultError('dedicated JWT config creation failed', 'config') from None
    try:
        expect(client.request('POST', 'auth/' + spec.mount + '/role/' + spec.role, spec.role_config), {200, 204})
    except VaultError:
        raise VaultError('dedicated JWT role creation failed', 'role') from None


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
