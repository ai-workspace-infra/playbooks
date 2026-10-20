#!/usr/bin/env python3
"""Prepare explicit candidate or primary application access without rotating auth."""
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
runtime_mode = spec.get('runtime_mode', 'candidate')
assert runtime_mode in ('candidate', 'primary'), 'unsupported runtime mode'
primary = runtime_mode == 'primary'
if primary:
    assert spec.get('source_writers_fenced') is True, 'source writer fence is required'
    assert spec.get('target_writers_paused') is True, 'target writers must be paused'
    backup_path = Path(spec['primary_backup_manifest'])
    assert backup_path.is_absolute() and not backup_path.is_symlink()
    assert str(backup_path).startswith('/data/backups/web-saas/' + generation + '/')
    backup = json.loads(backup_path.read_text())
    assert backup['hostname'] == 'web-saas-' + generation
    assert backup['restore_status'] == 'passed'
    assert backup['source_rows_sha256'] == backup['restored_rows_sha256']
    assert not backup['database_counts']['dirty']


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
if primary:
    # Runtime data access is deliberately separate from schema migration access.
    # Keep the existing RLS contract; never grant a superuser or schema ownership.
    from application_permissions import primary_permissions_sql
    sql(primary_permissions_sql())
else:
    assert sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname='public' AND c.relkind IN ('r','p') "
               "AND has_table_privilege('generation_app',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')") == '0', \
        'primary role cannot be silently converted back to a candidate'
    sql('GRANT CONNECT ON DATABASE account TO generation_app; GRANT USAGE ON SCHEMA public TO generation_app; '
        'GRANT SELECT ON ALL TABLES IN SCHEMA public TO generation_app; GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO generation_app; '
        'ALTER ROLE generation_app SET default_transaction_read_only=on;')
identity = {'Host': 'postgres', 'Port': 5432, 'Database': 'account', 'Role': role}
identity_sha = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
content = ('DATABASE_URL="host=postgres port=5432 dbname=account user=generation_app password=' + password + ' sslmode=disable"\n'
           'DATABASE_IDENTITY_SHA256=' + identity_sha + '\nDATABASE_RUNTIME_ROLE=primary\nDATABASE_BACKGROUND_WRITERS=' + str(primary).lower() + '\n')
path = root / 'generation-app.env'
changed = not path.exists() or path.read_text() != content
if changed:
    with path.open('w') as handle:
        handle.write(content)
path.chmod(0o600)
auth_sessions = not primary and spec.get('candidate_auth_sessions') is True
if primary:
    # The candidate Compose profile may still include this file last. Both
    # connections must resolve to the same business identity during handoff.
    auth_path = root / 'generation-auth.env'
    auth_changed = not auth_path.exists() or auth_path.read_text() != content
    if auth_changed:
        auth_path.write_text(content)
    auth_path.chmod(0o600)
    changed = changed or auth_changed
elif auth_sessions:
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
    assert sql("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'sessions' "
               "AND has_table_privilege('generation_auth',quote_ident(schemaname)||'.'||quote_ident(tablename),'INSERT,UPDATE,DELETE,TRUNCATE')") == '0', 'unexpected candidate authentication write privilege'
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
                  'business_read_only': not primary, 'candidate_auth_sessions': auth_sessions,
                  'runtime_mode': runtime_mode, 'background_writers': primary,
                  'credentials_rotated': False, 'changed': changed}))
