#!/usr/bin/env python3
"""Initialize and import only the authorized, isolated PROD V2 account DB."""
import json
import os
import pathlib
import socket
import subprocess

assert socket.gethostname() == 'web-saas-prod-v2'
base = pathlib.Path('/root/prod-v2-migration')
values = {}
for line in pathlib.Path('/etc/xcontrol/web-saas/secrets.env').read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key] = value


def sql(query, database='postgres'):
    result = subprocess.run(['docker', 'exec', '-i', 'web-saas-postgresql', 'sh', '-c', 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$1" -At -v ON_ERROR_STOP=1', 'sh', database],
        input=query, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit('V2 database operation failed; diagnostic detail suppressed')
    return result.stdout.strip()


if subprocess.check_output(['docker', 'ps', '--filter', 'name=^web-saas-accounts$', '-q'], text=True).strip():
    raise SystemExit('Accounts writer must be stopped for initial import')
if sql("SELECT count(*) FROM pg_roles WHERE rolname='account_user'") == '0':
    password = values['ACCOUNT_PG_PASSWORD']
    assert len(password) == 64 and all(c in '0123456789abcdef' for c in password)
    sql("CREATE ROLE account_user LOGIN PASSWORD '" + password + "';")
if sql("SELECT count(*) FROM pg_database WHERE datname='account'") == '0':
    sql('CREATE DATABASE account OWNER account_user;')
env = dict(os.environ, PROD_V2_DATABASE_URL='postgres://account_user:' + values['ACCOUNT_PG_PASSWORD'] + '@127.0.0.1:5432/account?sslmode=disable')
manifest = json.loads(subprocess.check_output([str(base/'migratectl-native'), 'native-schema'], text=True))
count = sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'", 'account')
if count == '0':
    subprocess.run([str(base/'migratectl-native'), 'init', '--dsn-env', 'PROD_V2_DATABASE_URL', '--environment', 'prod', '--schema-sha256', manifest['schema_sha256'], '--writers-paused', '--dry-run=false'], env=env, check=True)
else:
    print('V2 schema already initialized; never reset existing objects')

for preview in [True, False]:
    # Initial restore is deliberately not merge mode: merge excludes root,
    # whereas this newly initialized isolated target must restore all 30 users.
    args = [str(base/'migratectl-transfer'), 'import', '--dsn', env['PROD_V2_DATABASE_URL'], '--file', str(base/'selfhost-prod-users-import.yaml')]
    if preview:
        args += ['--dry-run']
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit('V2 user import failed: ' + result.stderr.replace(env['PROD_V2_DATABASE_URL'], '<redacted>')[:500])
    print(result.stdout.strip())

state = json.loads((base/'selfhost-prod-account-state.json').read_text())
assert len(state) == 30
payload = json.dumps(state)
assert '$prod_v2_state$' not in payload
sql("""WITH source AS (
 SELECT * FROM jsonb_to_recordset($prod_v2_state$""" + payload + """$prod_v2_state$::jsonb)
 AS t(uuid uuid,email text,active boolean,subscription_valid_from timestamptz,subscription_valid_until timestamptz)
) UPDATE users u SET active=s.active, subscription_valid_from=s.subscription_valid_from,
subscription_valid_until=s.subscription_valid_until
FROM source s WHERE u.uuid=s.uuid AND lower(u.email)=lower(s.email);""", 'account')
print('V2 users:', sql('SELECT count(*) FROM users', 'account'))
print('V2 sessions:', sql('SELECT count(*) FROM sessions', 'account'))
assert sql('SELECT count(*) FROM users', 'account') == '30'
assert sql('SELECT count(*) FROM sessions', 'account') == '0'
result = subprocess.run([str(base/'migratectl-transfer'), 'export', '--dsn', env['PROD_V2_DATABASE_URL'], '--output', str(base/'target-users-verified.yaml')], text=True, capture_output=True)
if result.returncode:
    raise SystemExit('Target export for identity verification failed')
print('Target verification snapshot ready')
