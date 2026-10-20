#!/usr/bin/env python3
"""Connect the pinned Accounts image to populated data on an isolated PG server."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

os.umask(0o077)


def run(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('isolated restore/runtime step failed')
    return result


def main(spec):
    generation = spec['target_generation']
    assert re.fullmatch(r'prod-v[1-9][0-9]*', generation)
    assert run(['hostname', '-s']).stdout.strip() == 'web-saas-' + generation
    root = Path(spec['checkpoint'])
    assert str(root).startswith('/data/backups/web-saas/' + generation + '/fresh-native-')
    proof = json.loads((root / 'post-sync-restore.json').read_text())
    restored = proof['isolated_restore_database']
    assert re.fullmatch(r'fresh_after_verify_[0-9]+', restored) and proof['result'] == 'passed'
    for key in ['postgres_image', 'accounts_image']:
        assert re.fullmatch(r'ghcr.io/[a-zA-Z0-9_./:-]+@sha256:[0-9a-f]{64}', spec[key])
    now = datetime.datetime.now(datetime.timezone.utc)
    pg = 'restore-runtime-' + now.strftime('%m%d%H%M%S%f')
    app = pg + '-accounts'
    result = {'run_id': spec['run_id'], 'candidate_commit': spec['candidate_commit'],
              'case_id': 'populated-restore-application-connection', 'started_utc': now.isoformat(),
              'accounts_image': spec['accounts_image'], 'postgres_image': spec['postgres_image'],
              'source_restore_archive_sha256': proof['archive_sha256'], 'source_restore_database': restored,
              'production_connections': False, 'status': 'FAIL', 'exit_code': 1}
    try:
        run(['docker', 'run', '-d', '--name', pg, '--network', 'none', '--memory=512m',
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=account',
             '--entrypoint', 'docker-entrypoint.sh', spec['postgres_image'], 'postgres'])
        for _ in range(60):
            if run(['docker', 'exec', pg, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], False).returncode == 0:
                break
            time.sleep(1)
        def query(container, database, sql):
            return run(['docker', 'exec', container, 'psql', '-U', 'postgres', '-d', database, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-c', sql]).stdout.strip()
        query(pg, 'account', 'CREATE ROLE generation_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS')
        dump = subprocess.Popen(['docker', 'exec', 'web-saas-postgresql', 'pg_dump', '-U', 'postgres', '-d', restored, '-Fc'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        restore = subprocess.run(['docker', 'exec', '-i', pg, 'pg_restore', '-U', 'postgres', '-d', 'account', '--no-owner', '--exit-on-error', '--single-transaction'],
                                 stdin=dump.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        dump.stdout.close()
        if dump.wait() or restore.returncode:
            raise RuntimeError('populated isolated restore failed')
        counts = "SELECT (SELECT count(*) FROM users)||':'||(SELECT count(*) FROM subscription_records)||':'||(SELECT count(*) FROM billing_ledger)"
        result['business_counts_equal'] = query(pg, 'account', counts) == query('web-saas-postgresql', restored, counts)
        query(pg, 'account', 'GRANT CONNECT ON DATABASE account TO generation_app; GRANT USAGE ON SCHEMA public TO generation_app; '
              'GRANT SELECT ON ALL TABLES IN SCHEMA public TO generation_app; GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO generation_app; '
              'ALTER ROLE generation_app SET default_transaction_read_only=on')
        auth_probe = spec.get('candidate_auth_sessions') is True
        if auth_probe:
            query(pg, 'account', 'GRANT INSERT, UPDATE, DELETE ON TABLE public.sessions TO generation_app; '
                  'ALTER ROLE generation_app SET default_transaction_read_only=off')
            query(pg, 'account', "INSERT INTO users(uuid,username,password,email,proxy_uuid,email_verified_at) VALUES "
                  "('00000000-0000-0000-0000-000000009901','login-fixture',crypt('isolated-fixture-password',gen_salt('bf')),"
                  "'login-fixture@example.invalid','00000000-0000-0000-0000-000000009902',now())")
            result['protected_writes_denied'] = query(pg, 'account', "SELECT bool_and(NOT has_table_privilege('generation_app',t,'INSERT,UPDATE,DELETE,TRUNCATE')) "
                  "FROM unnest(ARRAY['public.users','public.subscriptions','public.subscription_records','public.account_quota_states','public.billing_ledger','public.finance_payments','public.finance_refunds']) t") == 't'
        identity = {'Host': '127.0.0.1', 'Port': 5432, 'Database': 'account', 'Role': 'generation_app'}
        args = ['docker', 'run', '-d', '--name', app, '--network', 'container:' + pg, '--memory=384m',
                '--mount', 'type=bind,src=/etc/xcontrol/web-saas-recovery/account.yaml,dst=/app/config/account.yaml,readonly']
        environment = {'DATABASE_URL': 'host=127.0.0.1 port=5432 dbname=account user=generation_app sslmode=disable',
                       'DATABASE_IDENTITY_SHA256': hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest(),
                       'DATABASE_RUNTIME_ROLE': 'primary', 'DATABASE_BACKGROUND_WRITERS': 'false',
                       'INTERNAL_SERVICE_TOKEN': 'isolated-acceptance-fixture-token', 'AUTH_TOKEN_PUBLIC_TOKEN': 'isolated-fixture-public',
                       'AUTH_TOKEN_REFRESH_SECRET': 'isolated-fixture-refresh-not-production', 'AUTH_TOKEN_ACCESS_SECRET': 'isolated-fixture-access-not-production',
                       'CONFIG_TEMPLATE': '/app/config/account.yaml', 'CONFIG_PATH': '/tmp/account.yaml', 'IMAGE': spec['accounts_image'], 'PORT': '8080'}
        for key, value in environment.items():
            args += ['-e', key + '=' + value]
        run(args + [spec['accounts_image']])
        ready = False
        for _ in range(45):
            response = run(['docker', 'exec', app, 'curl', '-fsS', '--max-time', '2', 'http://127.0.0.1:8080/readyz'], False)
            if response.returncode == 0:
                ready = True
                break
            time.sleep(1)
        result['actual_accounts_ready'] = ready
        result['actual_database_connections'] = int(query(pg, 'account', "SELECT count(*) FROM pg_stat_activity WHERE datname='account' AND usename='generation_app'"))
        result['status'] = 'PASS' if ready and result['actual_database_connections'] > 0 and result['business_counts_equal'] else 'FAIL'
        if auth_probe and ready:
            login = run(['docker', 'exec', app, 'curl', '-sS', '--max-time', '10', '-w', '\n%{http_code}',
                         '-H', 'Content-Type: application/json', '--data', '{"identifier":"login-fixture@example.invalid","password":"isolated-fixture-password"}',
                         'http://127.0.0.1:8080/api/auth/login'], False)
            body, _, status = login.stdout.rpartition('\n')
            session = json.loads(body) if body.startswith('{') else {}
            result['login_http_status'] = int(status or '0')
            result['session_token_issued'] = isinstance(session.get('token'), str) and bool(session.get('token'))
            result['session_persisted'] = query(pg, 'account', "SELECT count(*) FROM sessions WHERE user_uuid='00000000-0000-0000-0000-000000009901'") == '1'
            if not (login.returncode == 0 and status == '200' and result['session_token_issued'] and result['session_persisted'] and result['protected_writes_denied']):
                result['status'] = 'FAIL'
        result['exit_code'] = 0 if result['status'] == 'PASS' else 1
    except Exception as error:
        result['error'] = type(error).__name__
    finally:
        for name in [app, pg]:
            run(['docker', 'stop', name], False)
    result['ended_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = root / ('restore-runtime-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(dict(result, evidence=str(path))))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main(json.load(sys.stdin)))
