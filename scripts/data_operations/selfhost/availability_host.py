#!/usr/bin/env python3
"""Read-only PROD runtime availability; no service changes or business rows."""
import json
import math
import re
import subprocess
import time

CONTAINERS = ('doco-cd', 'web-saas-postgresql', 'web-saas-accounts',
              'web-saas-billing', 'web-saas-console', 'web-saas-stunnel-server',
              'web-saas-stunnel-client', 'web-saas-caddy')
REPOSITORY = 'ai-workspace-infra/gitops'


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('availability check failed')
    return result.stdout.strip()


def healthy(name):
    state = command(['docker', 'inspect', '-f',
        '{{.State.Running}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', name])
    if state not in ('true:healthy', 'true:none') or (name in ('doco-cd', 'web-saas-caddy', 'web-saas-postgresql') and state != 'true:healthy'):
        raise RuntimeError('container is not healthy')


def metrics(raw):
    values = {}
    for line in raw.splitlines():
        match = re.fullmatch(r'(doco_cd_[a-z_]+)\{([^}]*)\} ([0-9.eE+\-]+)', line)
        if not match or REPOSITORY not in match[2]:
            continue
        value = float(match[3])
        if not math.isfinite(value) or value < 0:
            raise RuntimeError('invalid synchronization metric')
        values[match[1]] = values.get(match[1], 0) + value
    if 'doco_cd_polls_total' not in values or 'doco_cd_deployments_total' not in values:
        raise RuntimeError('synchronization evidence is missing')
    return values


def synchronized(before, after):
    return (after['doco_cd_polls_total'] > before['doco_cd_polls_total'] and
        after['doco_cd_deployments_total'] > after.get('doco_cd_deployment_errors_total', 0) and
        all(after.get(k, 0) == before.get(k, 0) for k in
            ('doco_cd_poll_errors_total', 'doco_cd_deployment_errors_total')) and
        all(after.get(k, 0) == 0 for k in
            ('doco_cd_deployments_active', 'doco_cd_deployments_queued')))


def http(container, port):
    addresses = command(['docker', 'inspect', '-f',
        '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}', container]).split()
    if not addresses:
        raise RuntimeError('API target address is missing')
    for path in ('/healthz', '/readyz'):
        code = command(['curl', '--noproxy', '*', '-sS', '--max-time', '10',
            '-o', '/dev/null', '-w', '%{http_code}', f'http://{addresses[0]}:{port}{path}'])
        if code != '200':
            raise RuntimeError('API health endpoint is unavailable')


def main():
    stage = 'containers'
    try:
        for name in CONTAINERS:
            healthy(name)
        stage = 'doco_sync'
        metric_args = ['curl', '--noproxy', '*', '-fsS', '--max-time', '10', 'http://127.0.0.1:9120/metrics']
        before = metrics(command(metric_args))
        deadline = time.monotonic() + 150
        while True:
            after = metrics(command(metric_args))
            if synchronized(before, after):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('synchronization did not complete')
            time.sleep(5)
        # Observe containers again after reconciliation, without stopping Caddy.
        stage = 'containers'
        for name in CONTAINERS:
            healthy(name)
        stage = 'https'
        for hostname in ('accounts.svc.plus', 'billing.svc.plus'):
            code = command(['curl', '--noproxy', '*', '-sS', '--max-time', '15',
                '--resolve', hostname + ':443:127.0.0.1', '-o', '/dev/null', '-w', '%{http_code}',
                'https://' + hostname + '/healthz'])
            if code != '200':
                raise RuntimeError('verified HTTPS is unavailable')
        stage = 'api'
        http('web-saas-accounts', 8080)
        http('web-saas-billing', 8081)
        stage = 'database'
        value = command(['docker', 'exec', 'web-saas-postgresql', 'psql', '-U', 'postgres',
            '-d', 'account', '-XAtq', '-v', 'ON_ERROR_STOP=1',
            '-c', 'BEGIN READ ONLY; SELECT 1; COMMIT;'])
        if value != '1':
            raise RuntimeError('database is unavailable')
        print(json.dumps(dict(schema=1, environment='prod', host='web-saas-prod',
            result='available', doco_synced=True, containers_healthy=True, caddy_running=True,
            https_available=True, api_available=True, db_available=True,
            target_writes=False, database_cutover_approved=False), sort_keys=True))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print(json.dumps(dict(schema=1, result='failed', failure_stage=stage,
            target_writes=False, database_cutover_approved=False)))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
