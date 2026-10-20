#!/usr/bin/env python3
"""Canonical Vault-backed application identity. Never generates a password."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.parse


ROLE = 'account_user'
RETIRED = ('recovery_app', 'generation_auth', 'generation_app')


def validate(spec):
    if not re.fullmatch(r'web-saas-prod-v[1-9][0-9]*', spec['target']):
        raise ValueError('invalid target')
    c = spec['contract']
    expected = dict(role=ROLE, database='account', host='postgres', port=5432,
                    sslmode='disable', backend_rls_bypass=True,
                    credential=dict(vault_path='kv/prod/databases', key='account_pg_password'))
    if c != expected:
        raise ValueError('canonical business credential contract changed')
    if not isinstance(spec['password'], str) or not spec['password'] or any(
            char in spec['password'] for char in '\r\n\0'):
        raise ValueError('invalid runtime password')
    if spec['action'] not in ('prepare', 'retire'):
        raise ValueError('invalid action')


def permissions_sql(password):
    # psql receives this only over stdin; never expose it or subprocess errors.
    literal = "'" + password.replace("'", "''") + "'"
    return r"""
BEGIN;
SET LOCAL standard_conforming_strings=on;
SET LOCAL lock_timeout='5s';
SELECT pg_advisory_xact_lock(73462, 1);
SELECT format('CREATE ROLE account_user LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L', PASSWORD_VALUE)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='account_user')
\gexec
DO $gate$
DECLARE r record; t record; s record;
BEGIN
 SELECT * INTO STRICT r FROM pg_roles WHERE rolname='account_user';
 IF r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
    OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid)
    OR EXISTS (SELECT 1 FROM pg_class WHERE relowner=r.oid)
    OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner=r.oid)
    OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner=r.oid)
    OR EXISTS (SELECT 1 FROM pg_database WHERE datdba=r.oid) THEN
   RAISE EXCEPTION 'business identity has unexpected administration or ownership';
 END IF;
 -- Native application backend contract, explicitly selected in GitOps.
 ALTER ROLE account_user BYPASSRLS;
 FOR t IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname IN ('public','finance') AND c.relkind IN ('r','p')
     AND c.relname NOT IN ('schema_migrations','accounts_controlled_migration_history')
     AND c.relname !~ '^(migratectl_|migration_)'
 LOOP
   EXECUTE format('GRANT USAGE ON SCHEMA %I TO account_user',t.nspname);
   EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE %I.%I TO account_user',t.nspname,t.relname);
 END LOOP;
 FOR s IN SELECT DISTINCT n.nspname,c.relname FROM pg_class c
   JOIN pg_namespace n ON n.oid=c.relnamespace
   JOIN pg_depend d ON d.objid=c.oid AND d.classid='pg_class'::regclass
   JOIN pg_class tab ON tab.oid=d.refobjid AND d.refclassid='pg_class'::regclass
   WHERE n.nspname IN ('public','finance') AND c.relkind='S' AND d.deptype IN ('a','i')
     AND tab.relname NOT IN ('schema_migrations','accounts_controlled_migration_history')
     AND tab.relname !~ '^(migratectl_|migration_)'
 LOOP
   EXECUTE format('GRANT USAGE,SELECT ON SEQUENCE %I.%I TO account_user',s.nspname,s.relname);
 END LOOP;
 IF has_database_privilege('account_user',current_database(),'CREATE')
    OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname IN ('public','finance')
      AND has_schema_privilege('account_user',n.oid,'CREATE'))
    OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname IN ('public','finance') AND c.relkind IN ('r','p') AND (
        has_table_privilege('account_user',c.oid,'TRUNCATE,TRIGGER,REFERENCES') OR (
          (c.relname IN ('schema_migrations','accounts_controlled_migration_history') OR c.relname ~ '^(migratectl_|migration_)')
          AND has_table_privilege('account_user',c.oid,'INSERT,UPDATE,DELETE')))) THEN
   RAISE EXCEPTION 'business identity has unexpected schema or migration privileges';
 END IF;
END $gate$;
GRANT CONNECT ON DATABASE account TO account_user;
GRANT USAGE ON SCHEMA public TO account_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO account_user;
ALTER ROLE account_user SET default_transaction_read_only=off;
ALTER ROLE account_user IN DATABASE account RESET default_transaction_read_only;
COMMIT;
""".replace('PASSWORD_VALUE', literal)


def env_content(password):
    identity = dict(Host='postgres', Port=5432, Database='account', Role=ROLE)
    digest = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
    uri = 'postgresql://account_user:' + urllib.parse.quote(password, safe='') + '@postgres:5432/account?sslmode=disable'
    return 'DATABASE_URL=' + uri + '\nDATABASE_IDENTITY_SHA256=' + digest + '\nDATABASE_RUNTIME_ROLE=primary\n'


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError('business identity step failed; credentials and SQL withheld')
    return result.stdout.strip()


def pg(query):
    return run(['docker','exec','-i','web-saas-postgresql','psql','-U','postgres','-d','account',
                '-XAtq','-v','ON_ERROR_STOP=1'], input=query)


def authenticate(password):
    info = json.loads(run(['docker','inspect','web-saas-postgresql']))[0]
    networks = [n for n,v in info['NetworkSettings']['Networks'].items() if 'postgres' in (v.get('Aliases') or [])]
    if len(networks) != 1:
        raise ValueError('one declared internal database network is required')
    # Credentials are inherited at runtime, never placed in command arguments.
    command = ['docker','run','--rm','--network',networks[0],'-e','PGPASSWORD',
               '--entrypoint','psql',info['Image'],'-h','postgres','-U',ROLE,'-d','account',
               '-XAtq','-v','ON_ERROR_STOP=1','-c','SELECT current_user']
    value = run(command, env=dict(os.environ, PGPASSWORD=password))
    if value != ROLE:
        raise ValueError('business authentication identity mismatch')
    # Prove this path authenticates, not a trust-rule false positive.
    denied = subprocess.run(command, env=dict(os.environ, PGPASSWORD=password+'-invalid-probe'),
                            capture_output=True, text=True, timeout=120)
    if denied.returncode == 0:
        raise ValueError('database authentication probe unexpectedly trusts an invalid password')


def backup_gate(spec):
    p = Path(spec['backup_manifest'])
    generation = spec['target'].removeprefix('web-saas-')
    if not p.is_absolute() or p.is_symlink() or p.resolve() != p or not str(p).startswith(
            '/data/backups/web-saas/'+generation+'/'):
        raise ValueError('backup manifest is not a canonical target path')
    b = json.loads(p.read_text())
    if not (b['hostname']==spec['target'] and b['restore_status']=='passed'
            and b['source_rows_sha256']==b['restored_rows_sha256']
            and re.fullmatch('[0-9a-f]{64}',b['source_rows_sha256'])
            and b['database_counts']['users']>0 and b['database_counts']['dirty'] is False):
        raise ValueError('populated target backup and isolated restore are required')
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(spec):
    validate(spec)
    if socket.gethostname().split('.')[0] != spec['target']:
        raise ValueError('actual target identity mismatch')
    backup_sha = backup_gate(spec)
    os.umask(0o077)
    root = Path('/etc/xcontrol/web-saas-recovery')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root/'business-database.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if spec['action']=='retire':
            for name in ('web-saas-accounts','web-saas-billing'):
                data=json.loads(run(['docker','inspect',name]))[0]
                env=dict(v.split('=',1) for v in data['Config']['Env'] if '=' in v)
                if not data['State']['Running'] or env.get('DATABASE_URL') != env_content(spec['password']).splitlines()[0].split('=',1)[1]:
                    raise ValueError('both running services must select the canonical identity')
            if pg("SELECT count(*) FROM pg_stat_activity WHERE usename IN ('recovery_app','generation_auth','generation_app')")!='0':
                raise ValueError('obsolete business sessions must drain before retirement')
            changed=pg("SELECT count(*) FROM pg_roles WHERE rolname IN ('recovery_app','generation_auth','generation_app') AND rolcanlogin")!='0'
            for role in RETIRED:
                pg("SELECT format('ALTER ROLE %I NOLOGIN',rolname) FROM pg_roles WHERE rolname='"+role+"' AND rolcanlogin\n\\gexec\n")
            obsolete_keys={'RECOVERY_APP_PASSWORD','DATABASE_URL','DATABASE_IDENTITY_SHA256','DATABASE_RUNTIME_ROLE'}
            for name in ('secrets.env','stripe-sandbox.env'):
                path=root/name
                if not path.exists():continue
                original=path.read_text()
                cleaned='\n'.join(line for line in original.splitlines() if line.split('=',1)[0] not in obsolete_keys)+'\n'
                if cleaned!=original:
                    stage=root/(name+'.new');stage.write_text(cleaned);stage.chmod(0o600);stage.replace(path)
                    changed=True
            for name in ('generation-app.env','generation-auth.env'):
                path=root/name
                if path.exists():
                    retained=root/(name+'.retired')
                    if retained.exists():raise ValueError('retained legacy env checkpoint already exists')
                    path.replace(retained);changed=True
        else:
            if pg("SELECT count(*) FROM pg_roles WHERE rolname='account_user'")=='1':
                authenticate(spec['password'])  # Never rotate an existing mismatch.
            fingerprint="SELECT md5((SELECT coalesce(string_agg(rolname||coalesce(rolconfig::text,'')||rolbypassrls::text,'' ORDER BY rolname),'') FROM pg_roles WHERE rolname='account_user')||(SELECT coalesce(string_agg(oid::text||coalesce(relacl::text,''),'' ORDER BY oid),'') FROM pg_class WHERE relnamespace IN (SELECT oid FROM pg_namespace WHERE nspname IN ('public','finance'))))"
            before=pg(fingerprint)
            pg(permissions_sql(spec['password']))
            authenticate(spec['password'])
            content=env_content(spec['password']); dest=root/'business-database.env'
            changed=not dest.exists() or dest.read_text()!=content
            if changed:
                stage=root/'business-database.env.new'
                stage.write_text(content); stage.chmod(0o600); stage.replace(dest)
            dest.chmod(0o600)
            after=pg(fingerprint)
            changed=changed or before!=after
        result=dict(target=spec['target'],action=spec['action'],role=ROLE,database='account',
                    credential_source='kv/prod/databases#account_pg_password',
                    backup_manifest_sha256=backup_sha,changed=changed,
                    password_rotated=False,traffic_or_writer_ownership_changed=False)
        print(json.dumps(result))


if __name__=='__main__':
    try:
        main(json.load(sys.stdin))
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__,
                          'detail':str(exc) if isinstance(exc,ValueError) else 'execution failed; secret diagnostics withheld'}))
        sys.exit(2)
