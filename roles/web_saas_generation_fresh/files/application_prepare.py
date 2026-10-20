#!/usr/bin/env python3
"""Prepare a read-only candidate database identity without rotating shared auth."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

os.umask(0o077)
spec = json.load(sys.stdin)
generation = spec['target_generation']
assert re.fullmatch(r'prod-v[1-9][0-9]*', generation)
assert subprocess.check_output(['hostname', '-s'], text=True).strip() == 'web-saas-' + generation
root = Path('/etc/xcontrol/web-saas-recovery')
values = dict(line.split('=', 1) for line in (root / 'secrets.env').read_text().splitlines() if '=' in line)
password = values['RECOVERY_APP_PASSWORD']
assert re.fullmatch('[0-9a-f]{64}', password)
proof = json.loads(Path(spec['checkpoint'], 'align.json').read_text())
assert proof['business']['full_business_equal'] is True
role = 'generation_app'


def sql(query):
    result = subprocess.run(['docker', 'exec', '-i', 'web-saas-postgresql', 'psql', '-U', 'postgres', '-d', 'account', '-XAtq', '-v', 'ON_ERROR_STOP=1'],
                            input=query, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('candidate application permission preparation failed')
    return result.stdout.strip()


exists = sql("SELECT count(*) FROM pg_roles WHERE rolname='generation_app'")
if exists == '0':
    sql("CREATE ROLE generation_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS PASSWORD '" + password + "'")
else:
    assert (root / 'generation-app.env').is_file(), 'existing role without its recorded runtime identity'
sql('GRANT CONNECT ON DATABASE account TO generation_app; GRANT USAGE ON SCHEMA public TO generation_app; '
    'GRANT SELECT ON ALL TABLES IN SCHEMA public TO generation_app; GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO generation_app; '
    'ALTER ROLE generation_app SET default_transaction_read_only=on;')
identity = {'Host': 'postgres', 'Port': 5432, 'Database': 'account', 'Role': role}
identity_sha = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
content = ('DATABASE_URL="host=postgres port=5432 dbname=account user=generation_app password=' + password + ' sslmode=disable"\n'
           'DATABASE_IDENTITY_SHA256=' + identity_sha + '\nDATABASE_RUNTIME_ROLE=primary\nDATABASE_BACKGROUND_WRITERS=false\n')
path = root / 'generation-app.env'
changed = not path.exists() or path.read_text() != content
if changed:
    with path.open('w') as handle:
        handle.write(content)
path.chmod(0o600)
auth_sessions = spec.get('candidate_auth_sessions') is True
if auth_sessions:
    auth_role = 'generation_auth'
    auth_path = root / 'generation-auth.env'
    if sql("SELECT count(*) FROM pg_roles WHERE rolname='generation_auth'") == '0':
        sql("CREATE ROLE generation_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS PASSWORD '" + password + "'")
    else:
        assert auth_path.is_file(), 'existing authentication role has no recorded runtime identity'
    sql('BEGIN; GRANT CONNECT ON DATABASE account TO generation_auth; GRANT USAGE ON SCHEMA public TO generation_auth; '
        'GRANT SELECT ON ALL TABLES IN SCHEMA public TO generation_auth; GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO generation_auth; '
        'GRANT INSERT, UPDATE, DELETE ON TABLE public.sessions TO generation_auth; '
        'ALTER ROLE generation_auth SET default_transaction_read_only=off; COMMIT;')
    assert sql("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='generation_auth' "
               "AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER') AND table_name<>'sessions'") == '0', 'unexpected candidate authentication write privilege'
    auth_identity = dict(identity, Role=auth_role)
    auth_sha = hashlib.sha256(json.dumps(auth_identity, separators=(',', ':')).encode()).hexdigest()
    auth_content = ('DATABASE_URL="host=postgres port=5432 dbname=account user=generation_auth password=' + password + ' sslmode=disable"\n'
                    'DATABASE_IDENTITY_SHA256=' + auth_sha + '\nDATABASE_RUNTIME_ROLE=primary\nDATABASE_BACKGROUND_WRITERS=false\n')
    auth_changed = not auth_path.exists() or auth_path.read_text() != auth_content
    if auth_changed:
        auth_path.write_text(auth_content)
    auth_path.chmod(0o600)
    changed = changed or auth_changed
print(json.dumps({'generation': generation, 'application_database': 'account', 'database_role': role,
                  'business_read_only': True, 'candidate_auth_sessions': auth_sessions,
                  'background_writers': False, 'credentials_rotated': False, 'changed': changed}))
