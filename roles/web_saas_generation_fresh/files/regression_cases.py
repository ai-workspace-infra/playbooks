#!/usr/bin/env python3
"""Execute non-destructive historical regression probes against one candidate."""
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

os.umask(0o077)
HISTORY = 'https://github.com/ai-workspace-services/knowledge/blob/main/content/02-iac-devops/cloud-infrastructure-devsecops-baseline/records/prod-v2-v3-20261019/'
CASES = {
    'public-sni-cert': ('20261019-prod-v3-public-sni-repair.zh.md', 'Trusted TLS, SAN and read-only certificate mounts for every candidate hostname'),
    'public-host-completeness': ('README.md', 'Public host routing matches the supplied stage declaration; www preserves path/query in its redirect'),
    'runtime-secret-injection': ('20261019-prod-v3-canonical-service-token.zh.md', 'Accounts, Billing and Console process tokens equal canonical Vault value'),
    'billing-ingest': ('20261020-billing-vector-ingest-p0.zh.md', 'Invalid ingest tokens rejected; valid isolated input persisted exactly once'),
    'exporter-vector': ('20261020-billing-vector-ingest-p0.zh.md', 'Fresh isolated Exporter -> Vector -> Billing -> usage/ledger data'),
    'agent-registration': ('20261019-prod-v3-canonical-service-token.zh.md', 'An isolated edge registers, receives configuration and connects'),
    'client-transport': ('20261019-prod-v3-vless-path-p0-release.zh.md', 'Generated VLESS path matches Xray and an isolated client connects'),
    'auth-callback': ('README.md', 'Isolated session, OAuth and Stripe callback paths execute correctly'),
    'schema-data-protection': ('v2-to-v3-snapshot-refresh-20261019.zh.md', 'Full migratectl readonly comparison proves business equality'),
    'repeat-deploy-restore': ('web-saas-generation-snapshot-recovery-template.zh.md', 'Same candidate replay and populated restore preserve state and application connectivity'),
}
PUBLIC_HOSTS = ['console.svc.plus', 'accounts.svc.plus', 'billing.svc.plus', 'xworktech.com', 'www.xworktech.com']


def validate_generations(target, source):
    pattern = r'prod-v([1-9][0-9]*)'
    target_match = re.fullmatch(pattern, target or '')
    source_match = re.fullmatch(pattern, source or '')
    if not target_match or not source_match:
        raise ValueError('target_generation and source_generation must be prod-vN values')
    if int(target_match.group(1)) != int(source_match.group(1)) + 1:
        raise ValueError('adjacent source and target generations are required')


def validate_public_route_expectations(expectations):
    if not isinstance(expectations, dict) or set(expectations) != set(PUBLIC_HOSTS):
        raise ValueError('public_route_expectations must declare exactly every public host')
    normalized = {}
    for host in PUBLIC_HOSTS:
        addresses = expectations[host]
        if not isinstance(addresses, list) or not addresses or any(not isinstance(address, str) for address in addresses):
            raise ValueError('each public host must have one or more declared A-record addresses')
        try:
            normalized[host] = sorted({str(ipaddress.IPv4Address(address)) for address in addresses})
        except ipaddress.AddressValueError as error:
            raise ValueError('public route expectation contains a non-IPv4 address') from error
    return normalized


def command(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=40)


def main(spec):
    generation = spec['target_generation']
    validate_generations(generation, spec.get('source_generation'))
    assert command(['hostname', '-s']).stdout.strip() == 'web-saas-' + generation
    address = str(ipaddress.ip_address(spec['target_ip']))
    case = spec['case_id']
    assert case in CASES
    assert re.fullmatch('[0-9a-f]{40}', spec['candidate_commit'])
    checkpoint = Path(spec['checkpoint'])
    assert str(checkpoint).startswith('/data/backups/web-saas/' + generation + '/fresh-native-')
    began = datetime.datetime.now(datetime.timezone.utc)
    result = {'run_id': spec['run_id'], 'candidate_commit': spec['candidate_commit'], 'source_generation': spec['source_generation'],
              'target_generation': generation, 'case_id': case, 'issue_ref': HISTORY + CASES[case][0], 'expected': CASES[case][1],
              'script': 'roles/web_saas_generation_fresh/files/regression_cases.py',
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'started_utc': began.isoformat(),
              'status': 'BLOCKED', 'exit_code': 2, 'checks': {}}
    containers = command(['docker', 'inspect', 'web-saas-accounts', 'web-saas-billing', 'web-saas-console', 'web-saas-caddy'])
    rows = json.loads(containers.stdout) if containers.returncode == 0 else []
    actual = {row['Name'].lstrip('/'): row for row in rows}

    def probe(host, path='/', method='GET', headers=None):
        args = ['curl', '-sS', '--connect-timeout', '3', '--max-time', '8', '--resolve', host + ':443:' + address,
                '-X', method, '-w', '\n__CODE__%{http_code}', 'https://' + host + path]
        for key, value in (headers or {}).items():
            args += ['-H', key + ': ' + value]
        if method == 'POST':
            args += ['-H', 'Content-Type: application/json', '--data', '{}']
        response = command(args)
        body, _, status = response.stdout.rpartition('\n__CODE__')
        return {'curl_exit_code': response.returncode, 'http_status': int(status or '0')}, body

    def verdict(passed):
        result['status'] = 'PASS' if passed else 'FAIL'
        result['exit_code'] = 0 if passed else 1

    public = PUBLIC_HOSTS
    aliases = [name + '-selfhost-' + generation + '.svc.plus' for name in ['console', 'accounts', 'billing']]
    if case == 'public-sni-cert':
        for host in public + aliases:
            result['checks'][host] = probe(host)[0]
        mounts = actual.get('web-saas-caddy', {}).get('Mounts', [])
        result['checks']['cert_mount_readonly'] = any(row.get('Destination') == '/etc/xcontrol/tls' and row.get('RW') is False for row in mounts)
        verdict(result['checks']['cert_mount_readonly'] and all(result['checks'][h]['curl_exit_code'] == 0 for h in public + aliases))
    elif case == 'public-host-completeness':
        expected_routes = validate_public_route_expectations(spec.get('public_route_expectations'))
        for host in public + aliases + ['postgresql-selfhost-' + generation + '.svc.plus']:
            try:
                request = urllib.request.Request('https://cloudflare-dns.com/dns-query?name=' + host + '&type=A', headers={'Accept': 'application/dns-json'})
                data = json.load(urllib.request.urlopen(request, timeout=8))
                addresses = sorted({row['data'] for row in data.get('Answer', []) if row.get('type') == 1})
            except Exception:
                addresses = []
            expected = expected_routes[host] if host in public else [address]
            result['checks'][host] = {'resolved': bool(addresses), 'expected_addresses': expected,
                                      'actual_addresses': sorted(addresses), 'expected_origin': sorted(addresses) == expected}
        redirect = command(['curl', '-sS', '-o', '/dev/null', '--max-time', '8', '--resolve', 'www.xworktech.com:443:' + address,
                            '-w', '%{http_code} %{redirect_url}', 'https://www.xworktech.com/candidate-check?value=1'])
        result['checks']['www_redirect_preserves_uri'] = redirect.returncode == 0 and redirect.stdout == '301 https://xworktech.com/candidate-check?value=1'
        verdict(result['checks']['www_redirect_preserves_uri'] and all(result['checks'][host]['expected_origin'] for host in public + aliases + ['postgresql-selfhost-' + generation + '.svc.plus']))
    elif case == 'runtime-secret-injection':
        expected = spec.get('expected_token', '')
        for name in ['web-saas-accounts', 'web-saas-billing', 'web-saas-console']:
            env = dict(value.split('=', 1) for value in actual.get(name, {}).get('Config', {}).get('Env', []) if '=' in value)
            result['checks'][name] = bool(expected) and hmac.compare_digest(env.get('INTERNAL_SERVICE_TOKEN', ''), expected)
        verdict(all(result['checks'].values()))
    elif case == 'billing-ingest':
        for label, method, path, headers, expected in [
            ('missing_token', 'POST', '/v1/ingest/snapshots', {}, 401),
            ('invalid_token', 'POST', '/v1/ingest/snapshots', {'Authorization': 'Bearer acceptance-invalid'}, 401),
            ('get_ingest', 'GET', '/v1/ingest/snapshots', {}, 403),
            ('management', 'GET', '/v1/status', {}, 403)]:
            response, _ = probe('billing.svc.plus', path, method, headers)
            result['checks'][label] = dict(response, expected_status=expected)
        negatives = all(row['http_status'] == row['expected_status'] and row['curl_exit_code'] == 0 for row in result['checks'].values())
        if not negatives:
            verdict(False)
        result['blocker'] = 'Isolated positive sample, usage/ledger persistence and duplicate-delivery fixture remain required'
    elif case == 'schema-data-protection':
        # Container recreation removes /tmp. Restore only a digest-verified tool
        # from the retained host checkpoint, never a floating rebuild.
        retained = Path(spec['retained_binary'])
        assert str(retained).startswith('/data/backups/web-saas/' + generation + '/')
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == spec['binary_sha256']
        prepared = command(['docker', 'cp', str(retained), 'web-saas-postgresql:' + spec['container_binary']])
        assert prepared.returncode == 0
        checked = command(['docker', 'exec', 'web-saas-postgresql', 'sha256sum', spec['container_binary']])
        assert checked.returncode == 0 and checked.stdout.split()[0] == spec['binary_sha256']
        source = 'postgresql://postgres@127.0.0.1:5432/' + spec['source_database'] + '?sslmode=disable'
        target = 'postgresql://postgres@127.0.0.1:5432/account?sslmode=disable'
        comparison = command(['docker', 'exec', '-e', 'SOURCE_DSN=' + source, '-e', 'TARGET_DSN=' + target, 'web-saas-postgresql',
                              spec['container_binary'], 'compare-full-business', '--source-dsn-env', 'SOURCE_DSN', '--target-dsn-env', 'TARGET_DSN',
                              '--environment', 'prod', '--writers-paused', '--schema-sha256', spec['schema_sha256'],
                              '--billing-schema-sha256', spec['billing_sha256'], '--dry-run=false'])
        data = json.loads(comparison.stdout) if comparison.returncode == 0 else {}
        result['checks'] = {'comparison_exit_code': comparison.returncode, 'full_business_equal': data.get('full_business_equal', False),
                            'source_read_only': data.get('source_read_only', False), 'target_writes': data.get('target_writes'), 'user_count': data.get('user_count')}
        verdict(comparison.returncode == 0 and data.get('full_business_equal') is True and data.get('target_writes') is False)
    else:
        result['blocker'] = {
            'exporter-vector': 'Candidate Billing is unavailable; no production collector is redirected or double-posted',
            'agent-registration': 'A separately provisioned writable isolated edge/Accounts fixture is required; production candidate business writes stay disabled',
            'client-transport': 'Depends on isolated edge registration and actual Xray/client connection',
            'auth-callback': 'Candidate OAuth/Stripe provider callbacks and isolated login are not configured; readiness does not prove callbacks',
            'repeat-deploy-restore': 'Populated restore is recorded separately; application-on-restored-database and healthy same-candidate convergence remain required',
        }[case]
    result['ended_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    folder = checkpoint / 'cases' / spec['candidate_commit']
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    result['evidence'] = str(folder / (case + '-' + began.strftime('%Y%m%dT%H%M%S%fZ') + '.json'))
    with Path(result['evidence']).open('x') as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main(json.load(sys.stdin)))
