#!/usr/bin/env python3
"""Target-local service readiness with verified HTTPS/SNI; no business writes."""
import ipaddress
import json
import subprocess
import sys
import urllib.request


class Refused(ValueError):
    pass


def require(value, message):
    if not value:
        raise Refused(message)


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, 'Candidate command or verified TLS probe failed')
    return result.stdout.strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused('Candidate endpoint redirected outside the explicit probe')


def http_probe(address, port, path, host, expected, html=False, token=None):
    ip = ipaddress.ip_address(address)
    require(ip.is_private and not ip.is_unspecified, 'Service address is not a target-local Docker address')
    authority = '[' + str(ip) + ']' if ip.version == 6 else str(ip)
    headers = {'Host': host}
    if token:
        headers['X-Service-Token'] = token
    request = urllib.request.Request('http://' + authority + ':' + str(port) + path, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        body = response.read(1024 * 1024)
        require(response.status == 200, 'Candidate endpoint did not return HTTP 200')
        if html:
            require('text/html' in response.headers.get('Content-Type', '') and b'<html' in body.lower(),
                    'Console endpoint did not return an actual HTML document')
        else:
            result = json.loads(body)
            require(isinstance(result, dict) and all(result.get(k) == v for k, v in expected.items()), 'Candidate API readiness response differs')


def sni_probe(domain, path, html=False):
    # curl verifies trust, SAN and expiry. No -k, custom DNS mutation, redirect,
    # authentication value, response body or raw certificate enters evidence.
    status = command(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*', '--proto', '=https',
        '--connect-timeout', '5', '--max-time', '15', '--max-redirs', '0', '--resolve', domain + ':443:127.0.0.1',
        '--output', '-' if html else '/dev/null', '--write-out', '\n%{http_code}' if html else '%{http_code}', 'https://' + domain + path])
    if html:
        body, separator, status = status.rpartition('\n')
        require(separator and '<html' in body.lower(), 'Console HTTPS/SNI endpoint did not return an actual HTML document')
    require(status == '200', 'Candidate HTTPS/SNI endpoint did not return HTTP 200')


def execute(request):
    contract, token = request['contract'], request['shared_token']
    probes = []
    specifications = (
        ('accounts', 'accounts', 8080, '/readyz', contract['target']['accounts_domain'], {'status': 'ready', 'database': 'ok'}),
        ('billing', 'billing-service', 8081, '/readyz', 'billing', {'status': 'ready', 'database': 'ok'}),
        ('content', 'content-service', 8084, '/healthz', 'content', {'status': 'ok'}),
        ('console', 'portal', 3000, '/', contract['target']['console_domain'], {}),
    )
    for service, key, port, path, host, expected in specifications:
        container = json.loads(command(['docker', 'inspect', 'web-saas-' + service]))[0]
        require(container['State']['Running'] and container['Config']['Image'] == contract['release']['services'][key],
                'Candidate runtime image or process differs')
        environment = dict(value.split('=', 1) for value in container['Config']['Env'] if '=' in value)
        require(environment.get('INTERNAL_SERVICE_TOKEN') == token and bool(token), 'Canonical shared token is absent from an actual service process')
        if service in ('accounts', 'billing'):
            require(environment.get('DATABASE_BACKGROUND_WRITERS') == 'false' and environment.get('DATABASE_RUNTIME_ROLE') == 'primary',
                    'Actual candidate process enables unmanaged schema or background writers')
            require(not any(environment.get(k) for k in ('STRIPE_SECRET_KEY', 'SMTP_PASSWORD', 'ROOT_BOOTSTRAP_PASSWORD')),
                    'Candidate process has external-effect credentials enabled')
        addresses = [item['IPAddress'] for item in container['NetworkSettings']['Networks'].values() if item.get('IPAddress')]
        require(addresses, 'Candidate Docker network address is absent')
        http_probe(addresses[0], port, path, host, expected, service == 'console')
        probes.append({'service': service, 'status': 'passed', 'check': 'http_document' if service == 'console' else 'api_ready'})
        if service == 'content':
            http_probe(addresses[0], port, '/api/v1/docs/home', host, {}, token=token)
            probes.append({'service': service, 'status': 'passed', 'check': 'authenticated_content_endpoint'})
    command(['docker', 'exec', 'web-saas-caddy', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile'])
    for key, path in (('console_domain', '/'), ('accounts_domain', '/readyz')):
        if key == 'console_domain':
            sni_probe(contract['target'][key], path, html=True)
        else:
            sni_probe(contract['target'][key], path)
        probes.append({'service': key, 'status': 'passed', 'check': 'trusted_https_sni'})
    if contract['target'].get('console_public_domain'):
        for path in ('/', '/login'):
            sni_probe(contract['target']['console_public_domain'], path, html=True)
        probes.append({'service': 'console_public_domain', 'status': 'passed', 'check': 'trusted_https_html'})
    if contract['target'].get('billing_domain'):
        sni_probe(contract['target']['billing_domain'], '/readyz')
        probes.append({'service': 'billing', 'status': 'passed', 'check': 'trusted_https_readiness_sni'})
    controller = json.loads(command(['docker', 'inspect', 'doco-cd']))[0]
    require(controller['State']['Running'] and controller['Config']['Image'] == contract['release']['doco_cd']['image'],
            'Actual Doco-CD controller image differs')
    settings = dict(value.split('=', 1) for value in controller['Config']['Env'] if '=' in value)
    selected = json.loads(settings['POLL_CONFIG'])
    require(len(selected) == 1 and selected[0].get('reference') == contract['resolved']['gitops_sha']
            and selected[0].get('target') == contract['release']['doco_cd']['target']
            and selected[0].get('url') == contract['release']['doco_cd']['repository'], 'Actual Doco-CD selection differs')
    return {'status': 'candidate_runtime_ready', 'probes': probes, 'business_acceptance': False, 'cutover': False,
            'blocked': [{'check': name, 'status': 'not_run', 'reason': 'requires separately authorized isolated business acceptance'}
                        for name in ('authenticated_business', 'oauth_callbacks', 'payment_webhooks', 'agent_connectivity', 'billing_ingestion_cycles')]}


if __name__ == '__main__':
    try:
        print(json.dumps(execute(json.load(sys.stdin)), sort_keys=True))
    except Exception:
        print('Candidate runtime readiness failed; private response and process data withheld.', file=sys.stderr)
        sys.exit(2)
