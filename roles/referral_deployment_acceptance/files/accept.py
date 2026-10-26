#!/usr/bin/env python3
"""Deployment-bound HTTPS preflight. Does not create users, Stripe objects or DB data."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

WORKLOADS = {'caddy', 'console', 'accounts', 'billing', 'content', 'postgresql'}

BUSINESS_CASES = ('registration_attribution', 'mfa_if_enabled', 'campaign_rules',
                  'ten_percent_no_payout', 'annual_30_days_unchanged_billing_anchor',
                  'seven_day_freeze', 'refund_reversal', 'dispute_reversal',
                  'real_stripe_webhook_delivery', 'replay_idempotency', 'business_dml_ddl_denial')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def origin(value):
    p = urllib.parse.urlsplit(value)
    require(p.scheme == 'https' and p.hostname and '.' in p.hostname
            and not p.username and not p.password and not p.query and not p.fragment
            and p.path in ('', '/') and p.port in (None, 443), 'exact trusted HTTPS origin required')
    return value.rstrip('/')


def validate(contract, observed, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    require(contract.get('schema') == 'referral-deployment/v1', 'unsupported contract')
    require(contract.get('environment') == 'dev' and contract.get('stage') in ('sit', 'uat'), 'nonproduction stage required')
    require(contract.get('feature_id') == 'xconnect-referral', 'unexpected feature')
    for field in ('sit_namespace', 'uat_namespace'):
        require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', contract.get(field, '')) is not None, 'explicit namespaces required')
    require(contract['sit_namespace'] != contract['uat_namespace'], 'SIT and UAT must be distinct')
    require(contract.get('namespace') == contract[contract['stage']+'_namespace'], 'stage namespace mismatch')
    for field in ('cluster', 'instance_id', 'run_id', 'candidate_fingerprint', 'gitops_sha', 'data_baseline', 'owner_sha'):
        require(isinstance(contract.get(field), str) and bool(contract[field]) and not contract[field].lower().startswith(('unknown', 'blocked')), 'complete candidate and target required')
    for field in ('candidate_fingerprint', 'gitops_sha', 'owner_sha'):
        require(re.fullmatch(r'[a-f0-9]{64}' if field == 'candidate_fingerprint' else r'[a-f0-9]{40}', contract[field]) is not None, 'invalid content identity')
    require(isinstance(contract.get('source_shas'), dict) and bool(contract['source_shas']), 'source matrix required')
    require(all(re.fullmatch(r'[a-f0-9]{40}', value or '') for value in contract['source_shas'].values()), 'full source SHAs required')
    if contract['stage'] == 'uat':
        require(contract.get('merge_shas') == contract['source_shas'], 'UAT must bind each actual merged source')
    origins = contract.get('origins', {})
    require(isinstance(origins, dict) and set(origins) == {'accounts', 'console'}, 'accounts/console route origins required')
    for value in origins.values():
        origin(value)
    images = contract.get('images', {})
    runtime = contract.get('expected_runtime_image_ids', {})
    require(isinstance(images, dict) and isinstance(runtime, dict) and set(images) == WORKLOADS and set(runtime) == set(images), 'full image matrix required')
    require(all(re.fullmatch(r'[^\s@]+@sha256:[a-f0-9]{64}', value or '') for value in images.values()), 'immutable image references required')
    require(all(re.fullmatch(r'(?:[^\s]+://)?(?:[^\s@]+@)?sha256:[a-f0-9]{64}', value or '') for value in runtime.values()), 'expected platform imageIDs required')
    # Keep OCI index digest and platform/runtime digest separate; never assume equality.
    require(observed.get('schema') == 'referral-deployment-observation/v1', 'owner deployment observation required')
    for field in ('environment', 'stage', 'feature_id', 'namespace', 'cluster', 'instance_id',
                  'candidate_fingerprint', 'gitops_sha', 'origins'):
        require(observed.get(field) == contract[field], 'observed deployment binding mismatch')
    sampled = dt.datetime.fromisoformat(observed['sampled_at'].replace('Z', '+00:00'))
    require(sampled.tzinfo is not None and 0 <= (now-sampled).total_seconds() <= 600, 'fresh deployment observation required')
    require(observed.get('flux_ready') is True and observed.get('observed_generation_current') is True, 'current Flux convergence required')
    pods = observed.get('workloads', {})
    require(isinstance(pods, dict) and set(pods) == set(images) and all(isinstance(value, dict) for value in pods.values()), 'all required workloads must be observed')
    for name in images:
        require(pods[name].get('namespace') == contract['namespace'] and pods[name].get('ready') is True
                and pods[name].get('image') == images[name] and pods[name].get('image_id') == runtime[name], 'runtime workload mismatch')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, method='GET', payload=None, headers=None):
    # No proxy credentials, cookies, bearer token or TLS verification bypass.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(url, data=payload, headers=headers or {}, method=method)
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(65537)
        require(len(data) <= 65536, 'oversized response')
        return response.status, data


def assess(contract, observed, transport=request, now=None):
    if not isinstance(contract, dict) or not isinstance(observed, dict):
        return {'schema': 'referral-deployment-acceptance/v1', 'result': 'BLOCKED', 'checks': {}}
    checks = {case: {'status': 'NOT_RUN', 'reason': 'real deployed business/Sandbox/DB owner execution required'} for case in BUSINESS_CASES}
    result = {'schema': 'referral-deployment-acceptance/v1', 'result': 'BLOCKED',
              'feature_id': contract.get('feature_id'), 'stage': contract.get('stage'),
              'namespace': contract.get('namespace'), 'candidate_fingerprint': contract.get('candidate_fingerprint'),
              'owner_sha': contract.get('owner_sha'), 'checks': checks}
    try:
        validate(contract, observed, now)
    except (ValueError, KeyError, TypeError, OverflowError):
        checks['deployment_binding'] = {'status': 'BLOCKED', 'reason': 'missing, stale or mismatched deployment/candidate evidence'}
        return result
    checks['deployment_binding'] = {'status': 'PASS'}
    result.update({key: contract[key] for key in ('run_id', 'cluster', 'instance_id', 'gitops_sha', 'source_shas', 'origins', 'images', 'expected_runtime_image_ids')})
    probes = [('accounts_ready', origin(contract['origins']['accounts'])+'/readyz', 'GET', None, 200),
              ('console_route', origin(contract['origins']['console'])+'/', 'GET', None, 200),
              ('accounts_runtime_image', origin(contract['origins']['accounts'])+'/api/ping', 'GET', None, 200),
              ('bad_signature_401', origin(contract['origins']['accounts'])+'/api/billing/stripe/webhook', 'POST', b'{}', 401)]
    for case, url, method, payload, expected in probes:
        try:
            status, body = transport(url, method, payload, {'Content-Type': 'application/json', 'Stripe-Signature': 't=0,v1=invalid'} if payload else {})
            passed = status == expected
            if case == 'bad_signature_401':
                response = json.loads(body)
                # Existing Accounts respondError uses a string error code.
                passed = passed and isinstance(response, dict) and response.get('error') == 'invalid_signature'
            if case == 'accounts_runtime_image':
                response = json.loads(body)
                passed = passed and isinstance(response, dict) and response.get('image') == contract['images']['accounts']
            checks[case] = {'status': 'PASS' if passed else 'FAIL', 'http_status': status}
        except (OSError, ValueError, TypeError):
            checks[case] = {'status': 'FAIL', 'reason': 'trusted HTTPS probe unavailable or contract mismatch'}
    # This partial adapter cannot turn readiness or local httptest into business PASS.
    if any(value['status'] == 'FAIL' for value in checks.values()):
        result['result'] = 'FAIL'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', required=True)
    parser.add_argument('--observation', required=True)
    parser.add_argument('--observation-sha256', required=True)
    args = parser.parse_args()
    try:
        raw = Path(args.observation).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == args.observation_sha256, 'observation artifact digest mismatch')
        result = assess(json.loads(Path(args.contract).read_text()), json.loads(raw))
    except (OSError, ValueError, KeyError, TypeError):
        result = {'schema': 'referral-deployment-acceptance/v1', 'result': 'BLOCKED', 'checks': {}, 'reason': 'invalid bound input artifacts'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['result'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
