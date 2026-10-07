#!/usr/bin/env python3
"""Short-lived qualification of fixed managed images; never admits business.

The service entrypoints are overridden deliberately. Accounts reads /dev/null,
never the image's legacy templates or any credential-expanded config. Neither
service receives a DB password; network=none and a closed loopback port make
database access unavailable even if the standby contract regresses.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from native_init_host import (Refused, command, registry_session, require,
                             remove_execution_container, validate_registry_credentials)

SERVICES = {
    'accounts': ('accounts', '/usr/local/bin/account', '8080'),
    'billing': ('billing-service', '/app/billing-service', '8081'),
}
PROBE_DSN = 'postgresql://managed_probe@127.0.0.1:1/account?sslmode=disable'
PROBE_IDENTITY = hashlib.sha256(json.dumps(
    dict(Host='127.0.0.1', Port=1, Database='account', Role='managed_probe'),
    separators=(',', ':')).encode()).hexdigest()


def validate_spec(spec):
    require(isinstance(spec, dict) and set(spec) == {'schema', 'environment', 'host', 'services'} and
            type(spec['schema']) is int and spec['schema'] == 1 and
            spec['environment'] == 'prod' and spec['host'] == 'web-saas-prod',
            'Managed runtime qualification target contract differs')
    require(isinstance(spec['services'], dict) and set(spec['services']) == set(SERVICES),
            'Both Accounts and Billing must be qualified together')
    for name, (repository, _, _) in SERVICES.items():
        service = spec['services'][name]
        require(isinstance(service, dict) and set(service) == {'commit', 'image', 'image_digest'} and
                isinstance(service['commit'], str) and re.fullmatch('[0-9a-f]{40}', service['commit']) and
                service['image'] == 'ghcr.io/ai-workspace-services/' + repository + ':sha-' + service['commit'] and
                isinstance(service['image_digest'], str) and re.fullmatch('sha256:[0-9a-f]{64}', service['image_digest']),
                'Managed runtime requires fixed source and image digest')


def standby_argv(name, service, container):
    require(name in SERVICES and re.fullmatch('managed-runtime-probe-[0-9a-f]{32}', container),
            'Managed qualification container is not owned')
    _, entrypoint, port = SERVICES[name]
    env = {
        'IMAGE': service['image'],
        'APP_ENV': 'prod',
        'DATABASE_RUNTIME_ROLE': 'standby',
        'DATABASE_BACKGROUND_WRITERS': 'false',
        'DATABASE_URL': PROBE_DSN,
        'DATABASE_IDENTITY_SHA256': PROBE_IDENTITY,
        'PORT': port,
    }
    argv = ['docker', 'run', '--detach', '--name', container, '--network', 'none',
            '--restart', 'no', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--pids-limit', '128',
            '--memory', '256m', '--cpus', '1', '--label', 'io.xworktech.owner=playbooks-managed-runtime-probe']
    for key, value in env.items():
        argv += ['--env', key + '=' + value]
    argv += ['--entrypoint', entrypoint, service['image'] + '@' + service['image_digest']]
    if name == 'accounts':
        argv += ['--config', '/dev/null']
    return argv


def inspect_container(name, service, container, expected_image=None):
    state = json.loads(command(['docker', 'inspect', container]))[0]
    _, entrypoint, _ = SERVICES[name]
    config = state['Config']; host = state['HostConfig']
    require(state['State']['Running'] is True and
            config['Image'] == (expected_image or service['image'] + '@' + service['image_digest']) and
            config['Entrypoint'] == [entrypoint] and
            (config.get('Cmd') == ['--config', '/dev/null'] if name == 'accounts' else not config.get('Cmd')) and
            host['NetworkMode'] == 'none' and host['RestartPolicy']['Name'] == 'no' and
            host['ReadonlyRootfs'] is True and 'ALL' in host.get('CapDrop', []) and
            'no-new-privileges' in host.get('SecurityOpt', []) and
            not state.get('Mounts') and not host.get('PortBindings') and
            config.get('Labels', {}).get('io.xworktech.owner') == 'playbooks-managed-runtime-probe',
            'Managed probe differs from isolated standby execution')
    environment = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
    require(environment.get('DATABASE_RUNTIME_ROLE') == 'standby' and
            environment.get('DATABASE_BACKGROUND_WRITERS') == 'false' and
            environment.get('DATABASE_URL') == PROBE_DSN and
            environment.get('DATABASE_IDENTITY_SHA256') == PROBE_IDENTITY and
            environment.get('IMAGE') == service['image'] and
            not any(environment.get(key) for key in
                    ('SUPABASE_CONNECT_URI', 'SUPABASE_CONNECT_URL', 'PGPASSWORD',
                     'POSTGRES_PASSWORD', 'DB_PASSWORD', 'DB_TLS_HOST', 'GHCR_TOKEN')),
            'Managed probe cannot retain database or registry credentials')


def probe(container, port, path, method='GET'):
    # nc is provided by Accounts' netcat-openbsd and Billing's Alpine BusyBox.
    # A raw loopback exchange handles every verb and preserves 503 bodies.
    require(port in ('8080', '8081') and path in ('/api/ping', '/healthz', '/readyz', '/api/users') and
            method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'), 'Managed probe request is not allowed')
    raw = command(['docker', 'exec', '-i', container, 'nc', '-w', '5', '127.0.0.1', port],
                  input=method + ' ' + path + ' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
    match = re.search(r'HTTP/\d(?:\.\d)?\s+(\d{3})[^\r\n]*', raw)
    require(match is not None, 'Managed probe response is unavailable')
    status = int(match.group(1))
    start = raw.find('{')
    body = json.loads(raw[start:]) if start >= 0 else None
    return status, body


def verify_probes(name, service, container):
    port = SERVICES[name][2]
    for _ in range(20):
        try:
            status, body = probe(container, port, '/api/ping')
            break
        except (Refused, ValueError):
            time.sleep(0.25)
    else:
        raise Refused('Managed standby listener did not become available')
    expected = dict(image=service['image'], tag='sha-' + service['commit'], commit=service['commit'],
                    database_role='standby', bootstrap_writes=False, proxy_uuid_rotator=False,
                    background_writers=False, business_requests_enabled=False,
                    configured_database_sha256=PROBE_IDENTITY, schema_version=0, schema_management='external')
    require(status == 200 and isinstance(body, dict) and
            all(body.get(key) == value and type(body.get(key)) is type(value) for key, value in expected.items()),
            'Managed standby release or runtime metadata differs')
    require(probe(container, port, '/healthz')[0] == 200 and
            probe(container, port, '/readyz')[0] == 503,
            'Managed standby liveness or readiness differs')
    for method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
        status, body = probe(container, port, '/api/users', method)
        require(status == 503 and isinstance(body, dict) and
                body.get('reason') == 'database_runtime_standby',
                'Managed standby admitted a business request')


def qualify(service_name, service, env):
    container = 'managed-runtime-probe-' + uuid.uuid4().hex
    try:
        command(standby_argv(service_name, service, container), env=env)
        inspect_container(service_name, service, container)
        verify_probes(service_name, service, container)
    finally:
        remove_execution_container(container)


def execute(spec, dry_run, credentials):
    validate_spec(spec)
    require(type(dry_run) is bool and (dry_run or os.environ.get('MANAGED_RUNTIME_GATE_VERIFIED') == 'true'),
            'Managed image execution requires verified production qualification approval')
    validate_registry_credentials(credentials)
    with registry_session(credentials) as env:
        for name, service in spec['services'].items():
            command(['docker', 'pull', service['image'] + '@' + service['image_digest']], env=env)
            if not dry_run:
                qualify(name, service, env)
    return dict(schema=1, environment='prod', host='web-saas-prod',
                stage='managed_image_preview' if dry_run else 'managed_images_qualified',
                services=spec['services'], runtime_role='standby', database_connected=False,
                schema_verified=False, business_requests_enabled=False, background_writers=False,
                application_deployed=False, database_cutover_approved=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--dry-run', choices=('true', 'false'), required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text()); validate_spec(spec)
    if args.validate_only:
        return
    raw = sys.stdin.readline(65537)
    require(len(raw.encode()) <= 65536 and raw.endswith('\n'), 'Private registry input is incomplete')
    credentials = json.loads(raw); validate_registry_credentials(credentials)
    print(json.dumps(execute(spec, args.dry_run == 'true', credentials), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Managed image qualification refused; private inputs and raw service logs are withheld.', file=sys.stderr)
        sys.exit(1)
