#!/usr/bin/env python3
"""Real external Dev BFF registration; tokens/cookies remain in process memory."""
import argparse
import hashlib
import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit
import accept as preflight

class Blocked(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


class HTTPS:
    def __init__(self, origin):
        self.host = urlsplit(preflight.origin(origin)).hostname
        self.cookies = SimpleCookie()

    def __call__(self, path, method='GET', payload=None):
        # No redirect, proxy, auth file, TLS override or cookie persistence.
        require(path.startswith('/api/') and '?' not in path, 'fixed API route required')
        connection = http.client.HTTPSConnection(self.host, context=ssl.create_default_context(), timeout=15)
        try:
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            if self.cookies:
                headers['Cookie'] = '; '.join(k+'='+v.value for k,v in self.cookies.items())
            connection.request(method, path, body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
            response = connection.getresponse()
            raw = response.read(65537)
            require(len(raw) <= 65536, 'bounded API response required')
            for key,value in response.getheaders():
                if key.lower() == 'set-cookie':
                    jar = SimpleCookie();jar.load(value)
                    for name,cookie in jar.items():
                        require(not cookie['domain'] or cookie['domain'].lstrip('.') == self.host, 'cross-host cookie rejected')
                        self.cookies[name] = cookie.value
            body = json.loads(raw)
            require(isinstance(body,dict), 'JSON object response required')
            return response.status, body
        finally:
            connection.close()


def validate_samples(contract, samples):
    require(isinstance(samples, dict) and set(samples) == {'owner','invitee'}, 'two explicit isolated samples required')
    run = contract['run_id']
    require(re.fullmatch(r'[a-z0-9-]{8,48}', run) is not None, 'bounded unique sample run ID required')
    for role, sample in samples.items():
        require(isinstance(sample,dict) and set(sample) <= {'name','email','password','code'}, 'unsupported sample input')
        require(sample.get('name') == 'referral-'+run+'-'+role, 'run-scoped sample name required')
        email = sample.get('email','')
        require(email.startswith(sample['name']+'@') and email.endswith('.onwalk.net'), 'dedicated run-scoped onwalk test mailbox required')
        require(isinstance(sample.get('password'),str) and len(sample['password']) >= 16, 'independent strong sample password required')
        require(isinstance(sample.get('code'),str) and re.fullmatch(r'[0-9]{6}', sample['code']), 'real sample mailbox verification code required')
    require(samples['owner']['email'] != samples['invitee']['email'] and samples['owner']['password'] != samples['invitee']['password'], 'independent sample identities required')


def execute(contract, observed, samples, factory=HTTPS, now=None, health_transport=preflight.request):
    report = {'schema':'referral-live-registration/v1','result':'BLOCKED','checks':{},
              'registration_attribution':'BLOCKED','sandbox_acceptance':'NOT_RUN','business_acceptance':'NOT_RUN'}
    try:
        preflight.validate(contract, observed, now)
        validate_samples(contract,samples)
        health = preflight.assess(contract,observed,health_transport,now)
        require(all(health['checks'].get(key,{}).get('status') == 'PASS' for key in ('deployment_binding','accounts_ready','console_route','accounts_runtime_image')), 'fresh bound HTTPS preflight required')
        report.update({k:contract[k] for k in ('run_id','namespace','namespace_uid','candidate_fingerprint','gitops_sha')})
        owner = factory(contract['origins']['console']);invitee = factory(contract['origins']['console'])
        status, body = owner('/api/referrals/campaign')
        campaign = body.get('campaign',{})
        require(status == 200 and campaign.get('active') is True and campaign.get('enabled') is True, 'active reviewed campaign required; runner never changes it')
        require(campaign.get('cashbackBps') == 1000 and campaign.get('annualBonusDays') == 30, 'campaign API contract mismatch')
        report['checks']['campaign_read'] = 'PASS'
        code = None
        for role, client in (('owner',owner),('invitee',invitee)):
            payload = dict(samples[role])
            if role == 'invitee':payload['referralCode'] = code
            status, body = client('/api/auth/register','POST',payload)
            require(status in (200,201) and body.get('success') is True, role+' registration unavailable; inspect authorized mailbox/configuration owner')
            report['checks'][role+'_registration'] = 'PASS'
            status, body = client('/api/auth/login','POST',{'email':samples[role]['email'],'password':samples[role]['password']})
            require(not any(body.get(k) for k in ('needMfa','mfaRequired','mfa_required')), 'MFA challenge requires supported independent MFA adapter; not bypassed')
            require(status == 200 and body.get('success') is True, role+' BFF login unavailable')
            report['checks'][role+'_bff_session'] = 'PASS'
            status, body = client('/api/auth/referrals')
            require(status == 200 and isinstance(body.get('code'),str) and re.fullmatch(r'[A-Z0-9]{4,32}',body['code']), 'authenticated referral overview unavailable')
            if role == 'owner':code = body['code']
            else:require(body['code'] != code, 'distinct sample owner scopes required')
            report['checks'][role+'_overview'] = 'PASS'
        # Repeat the exact attributed registration through BFF: no manual store seed.
        status, body = invitee('/api/auth/register','POST',{**samples['invitee'],'referralCode':code})
        require(status == 409 and body.get('error') == 'email_already_exists', 'duplicate registration must be rejected')
        report['checks']['duplicate_registration_rejected'] = 'PASS'
        report['sample_ids_sha256'] = {role:hashlib.sha256(sample['email'].encode()).hexdigest() for role,sample in samples.items()}
        report['reason'] = 'HTTPS registration executed; unpaid attribution is not exposed by overview API. Independent bound DB audit evidence required; payment, MFA coverage and Sandbox remain incomplete.'
    except (Blocked,ValueError,KeyError,TypeError,OSError):
        # Never serialize exception, credentials, cookies, response body or tokens.
        report['reason'] = 'Blocked or failed external registration contract; completed individual checks retained, no total PASS.'
    return report


def main():
    p = argparse.ArgumentParser()
    for name in ('contract','observation','observation-sha256'):p.add_argument('--'+name,required=True)
    a = p.parse_args()
    try:
        raw = Path(a.observation).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == a.observation_sha256, 'observation integrity mismatch')
        # Caller injects only newly scoped sample values; no secret-file/CLI inputs.
        samples = json.loads(os.environ['DEV_REFERRAL_SAMPLES_JSON'])
        report = execute(json.loads(Path(a.contract).read_text()),json.loads(raw),samples)
    except (OSError,KeyError,TypeError,ValueError):
        report = {'schema':'referral-live-registration/v1','result':'BLOCKED','reason':'Missing authenticated observation or isolated runtime samples'}
    print(json.dumps(report,sort_keys=True))
    return 2  # This partial runner can never certify full referral acceptance.

if __name__ == '__main__':raise SystemExit(main())
