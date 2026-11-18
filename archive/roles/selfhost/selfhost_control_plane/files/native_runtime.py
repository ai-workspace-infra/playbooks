#!/usr/bin/env python3
"""Target-local fresh installation guard; domain SQL stays in native owners."""
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

import native_init_host as native
import business_database as business


def schema_fingerprint():
    raw = native.command(['docker', 'exec', 'web-saas-postgresql', 'pg_dump', '-U', 'postgres',
                          '-d', 'account', '--schema-only', '--schema=public', '--schema=finance', '--no-owner', '--no-acl'])
    value = '\n'.join(line for line in raw.splitlines()
                      if not line.startswith(('\\restrict ', '\\unrestrict ', '--')))
    return hashlib.sha256(value.encode()).hexdigest()


def binding(contract):
    return {'target': contract['target']['host'], 'resource_id': contract['resolved']['resource_id'],
            'environment': contract['environment'], 'services': contract['release']['services'],
            'accounts': contract['release']['accounts'], 'billing': contract['release'].get('billing'),
            'storage': contract['resolved']['data_disk']}


def verify_storage(storage):
    path = Path(storage['data_path'])
    native.require(not path.is_symlink(), 'Database path must not be a symlink')
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    native.require(ancestor.resolve() == ancestor, 'Database path traverses a symlink')
    if storage['profile'] == 'root':
        root = native.command(['findmnt', '-nro', 'SOURCE,UUID', '--mountpoint', '/'])
        actual = native.command(['findmnt', '-nro', 'SOURCE,UUID', '--target', str(ancestor)])
        native.require(actual == root, 'Bundled root storage must reside on the root filesystem')
    else:
        actual = native.command(['findmnt', '-nro', 'SOURCE', '--mountpoint', storage['mount_path']])
        native.require(native.command(['readlink', '-f', actual]) ==
                       native.command(['readlink', '-f', storage['device_path']]), 'Dedicated mount device differs')
        native.require(Path(storage['mount_path']) in path.parents, 'Data path is outside declared mount')
    if storage.get('filesystem_uuid'):
        native.require(native.command(['findmnt', '-nro', 'UUID', '--target', str(ancestor)]) ==
                       storage['filesystem_uuid'], 'Filesystem UUID differs')


def inspect(contract, state):
    verify_storage(contract['resolved']['data_disk'])
    data = Path(contract['resolved']['data_disk']['data_path'])
    if state.exists():
        recorded = json.loads(state.read_text())
        native.require(recorded['binding'] == binding(contract), 'Existing installation belongs to a different contract')
        return recorded
    native.require(not data.exists() or data.is_dir() and not any(data.iterdir()),
                   'Unowned database directory is not empty; explicit recovery required')
    native.require(not Path('/opt/doco-cd/docker-compose.yml').exists(), 'Unowned controller configuration already exists')
    if shutil.which('docker'):
        names = native.command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
        native.require(not any(name == 'doco-cd' or name.startswith('web-saas-') for name in names),
                       'Unowned managed containers already exist')
    return {'binding': binding(contract), 'initialized': False}


def save(state, record):
    # Atomic target-only journal; contains no credentials or DSNs.
    temporary = state.with_suffix('.new')
    with temporary.open('w', encoding='utf-8') as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(record, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(state)


def verify_initialized(contract, record):
    native.require(record.get('initialized') is True, 'Native initialization receipt is absent')
    native.require(record.get('billing_schema_sha256') == contract['release']['accounts']['billing_schema_sha256'],
                   'Existing target lacks the reviewed Billing DDL receipt; explicit backup/schema upgrade required')
    checkpoint = native.sql('SELECT version::text||\':\'||dirty::text FROM public.schema_migrations', 'account')
    native.require(checkpoint == str(contract['release']['accounts']['migration_version']) + ':false',
                   'Native checkpoint changed or is dirty')
    identity = native.sql("SELECT system_identifier::text FROM pg_control_system()") + ':' + \
        native.sql("SELECT oid::text FROM pg_database WHERE datname='account'")
    native.require(record['database_identity'] == identity and record['schema_fingerprint'] == schema_fingerprint(),
                   'Initialized database identity or schema changed')
    return identity


def apply_fresh_billing(contract, artifact):
    """Only the fixed, reviewed Billing-owned bootstrap DDL; no domain SQL copy."""
    native.require(isinstance(artifact, dict) and artifact.get('source_sha') == contract['release']['billing']['source_sha']
                   and artifact.get('schema_sha256') == contract['release']['accounts']['billing_schema_sha256']
                   and hashlib.sha256(artifact.get('sql', '').encode()).hexdigest() == artifact['schema_sha256'],
                   'Billing artifact differs from the immutable source/schema pins')
    # Bootstrap permits only additive table/index DDL. Never execute a seed,
    # overwrite, session command or psql metacommand from an arbitrary artifact.
    sql = re.sub(r'--[^\n]*', '', artifact['sql'])
    statements = [part.strip() for part in sql.split(';') if part.strip()]
    native.require(statements and all(re.match(r'^CREATE (?:TABLE IF NOT EXISTS public\.[a-z_][a-z0-9_]*\s*\(|(?:UNIQUE )?INDEX IF NOT EXISTS [a-z_][a-z0-9_]*\s+ON public\.[a-z_][a-z0-9_]*\s*\()', part,
                   re.IGNORECASE) and '\\' not in part and not re.search(r'\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|COPY|DO|EXECUTE|SELECT)\b',
                   re.sub(r'ON DELETE CASCADE', '', part, flags=re.IGNORECASE), re.IGNORECASE) for part in statements),
                   'Billing bootstrap must contain only reviewed additive table/index DDL, no seeds')
    tables = json.loads(native.sql("SELECT coalesce(json_agg(tablename),'[]') FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_migrations'", 'account'))
    native.require(all(re.fullmatch('[a-z_][a-z0-9_]*', name) for name in tables), 'Unexpected table identifiers')
    empty = 'SELECT ' + ' + '.join('(SELECT count(*) FROM public."' + name + '")' for name in tables)
    native.require(tables and native.sql(empty, 'account') == '0', 'Billing bootstrap refuses nonempty targets')
    native.sql('BEGIN; SET LOCAL lock_timeout=\'15s\'; SET LOCAL statement_timeout=\'5min\';\n' +
               artifact['sql'] + '\nCOMMIT;', 'account')
    expected = re.findall(r'CREATE TABLE IF NOT EXISTS public\.([a-z_][a-z0-9_]*)', sql, re.IGNORECASE)
    native.require(expected and all(native.sql("SELECT to_regclass('public." + name + "') IS NOT NULL", 'account') == 't'
                                   for name in expected), 'Billing table verification failed')
    return artifact['schema_sha256']


def stunnel_reader(contract, credentials):
    image = contract['resolved']['selections'][1]['images']['stunnel-server']
    native.require(re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', image), 'Pinned stunnel reader image required')
    with native.registry_session(credentials) as env:
        native.command(['docker', 'pull', image], env=env)
        values = [native.command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'id', image, flag], env=env)
                  for flag in ('-u', '-g')]
    native.require(all(re.fullmatch(r'(0|[1-9][0-9]*)', value) for value in values), 'Invalid stunnel reader identity')
    return {'uid': int(values[0]), 'gid': int(values[1])}


def reload_service_config(name, paths):
    if name not in native.command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines():
        return {'changed': False}
    state = json.loads(native.command(['docker', 'inspect', '--format', '{{json .State}}', name]))
    started = datetime.fromisoformat(state['StartedAt'].replace('Z', '+00:00')).timestamp()
    changed = any(Path(path).stat().st_mtime > started for path in paths)
    if changed:
        # Existing stunnel caches its trust context; restart remounts atomic file replacements.
        native.command(['docker', 'restart', name])
    return {'changed': changed}


def reload_host_config():
    services = [
        ('web-saas-stunnel-client', ('/etc/xcontrol/web-saas/certs/ca-cert.pem', '/etc/xcontrol/web-saas/config/stunnel-client.conf')),
        ('web-saas-caddy', ('/etc/xcontrol/web-saas/Caddyfile',)),
    ]
    changed = [name for name, paths in services if reload_service_config(name, paths)['changed']]
    return {'changed': bool(changed), 'services': changed}


def execute(request):
    contract = request['contract']
    action = request['action']
    native.require(action in ('preflight', 'claim', 'stunnel_reader', 'reload_host_config', 'storage', 'initialize', 'verify'), 'Unknown native stage')
    data = Path(contract['resolved']['data_disk']['data_path'])
    state = data.parent / 'control-plane-owner.json'
    record = inspect(contract, state)
    if action == 'preflight':
        return {'managed': state.exists(), 'initialized': record['initialized'], 'changed': False}
    os.umask(0o077)
    if not state.parent.exists():
        native.require(action == 'claim' and contract['resolved']['data_disk']['profile'] == 'root',
                       'Dedicated storage parent must already be mounted')
        state.parent.mkdir(mode=0o700, parents=True)
        verify_storage(contract['resolved']['data_disk'])
    native.require(state.parent.is_dir(), 'Declared storage parent must be a directory')
    with (state.parent / 'control-plane-owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = inspect(contract, state)
        if action == 'claim':
            changed = not state.exists()
            if changed:
                save(state, record)
            return {'managed': True, 'initialized': record['initialized'], 'changed': changed}
        native.require(state.exists(), 'Target must be claimed before preparation')
        credentials = request['credentials']
        native.validate_credentials(credentials)
        if action == 'reload_host_config':
            return reload_host_config()
        if action == 'stunnel_reader':
            return stunnel_reader(contract, credentials)
        if action == 'storage':
            image = contract['release']['services']['postgresql']
            with native.registry_session(credentials) as env:
                native.command(['docker', 'pull', image], env=env)
                ids = [native.command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'id', image, flag, 'postgres'], env=env)
                       for flag in ('-u', '-g')]
            native.require(all(re.fullmatch('[1-9][0-9]*', value) for value in ids), 'PostgreSQL must use an unprivileged identity')
            changed = not data.exists()
            if changed:
                data.mkdir(mode=0o700)
                os.chown(data, int(ids[0]), int(ids[1]))
            else:
                native.require(data.stat().st_uid == int(ids[0]) and data.stat().st_gid == int(ids[1]),
                               'Existing database directory ownership differs; no automatic recursive ownership change')
            return {'changed': changed}
        info = json.loads(native.command(['docker', 'inspect', 'web-saas-postgresql']))[0]
        native.require(info['Config']['Image'] == contract['release']['services']['postgresql'] and
                       any(v['Type'] == 'bind' and v['Source'] == str(data) and v['Destination'] == '/var/lib/postgresql/data'
                           for v in info['Mounts']), 'Existing PostgreSQL image or data binding differs')
        native.require(re.fullmatch(r'17[0-9]{4}', native.sql('SHOW server_version_num')), 'Qualified PostgreSQL 17 is required')
        if record['initialized']:
            verify_initialized(contract, record)
            business.authenticate(request['business_password'])
            return {'changed': False, 'initialized': True}
        native.require(action == 'initialize' and contract['execution']['initialize'] is True,
                       'New target requires explicit initialize eligibility')
        names = native.command(['docker', 'ps', '--format', '{{.Names}}']).splitlines()
        native.require(not set(names).intersection({'web-saas-accounts', 'web-saas-billing', 'doco-cd'}),
                       'Target writers and reconciler must be paused before initialization')
        accounts = contract['release']['accounts']
        spec = {'schema_sha256': accounts['schema_sha256'], 'migration_version': accounts['migration_version'],
                'business_tables': accounts['business_tables'], 'business_table_count': len(accounts['business_tables'])}
        image = contract['release']['services']['accounts']
        native.require(image.startswith('ghcr.io/'), 'Native qualification currently requires the GHCR image owner')
        with native.registry_session(credentials) as env:
            native.command(['docker', 'pull', image], env=env)
            manifest = json.loads(native.command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/migratectl', image, 'native-schema'], env=env))
            native.validate_manifest(manifest, spec)
            present = native.sql("SELECT count(*) FROM pg_database WHERE datname='account'")
            native.require(present in ('0', '1'), 'Cannot establish database existence')
            if present == '0':
                native.command(['docker', 'exec', 'web-saas-postgresql', 'createdb', '-U', 'postgres', '--template=template0', '--encoding=UTF8', 'account'])
            flags = ['init', '--dsn-env=NATIVE_TARGET_DSN', '--environment=' + contract['environment'],
                     '--schema-sha256=' + spec['schema_sha256'], '--writers-paused', '--lock-timeout=15s', '--statement-timeout=5m']
            preview = json.loads(native.run_tool(image, 'control-plane-preview-' + uuid.uuid4().hex,
                                                flags + ['--dry-run=true'], credentials, env))
            native.require(preview.get('result') == 'eligible' and preview.get('business_rows') == 0,
                           'Native initialization is not eligible')
            receipt = json.loads(native.run_tool(image, 'control-plane-init-' + uuid.uuid4().hex,
                                                flags + ['--dry-run=false'], credentials, env))
        native.require(receipt.get('result') == 'initialized' and receipt.get('business_rows') == 0
                       and receipt.get('schema_sha256') == spec['schema_sha256']
                       and receipt.get('database_cutover_approved') is False, 'Native initialization receipt differs')
        billing_hash = apply_fresh_billing(contract, request.get('billing'))
        # Reuse the existing domain permission owner; never duplicate grants or rotate passwords.
        if native.sql("SELECT count(*) FROM pg_roles WHERE rolname='account_user'") == '1':
            business.authenticate(request['business_password'])
        business.pg(business.permissions_sql(request['business_password']))
        business.authenticate(request['business_password'])
        record.update(initialized=True, billing_schema_sha256=billing_hash, schema_fingerprint=schema_fingerprint(),
                      database_identity=native.sql('SELECT system_identifier::text FROM pg_control_system()') + ':' +
                      native.sql("SELECT oid::text FROM pg_database WHERE datname='account'"))
        save(state, record)
        verify_initialized(contract, record)
        return {'changed': True, 'initialized': True, 'business_acceptance': False}


if __name__ == '__main__':
    try:
        print(json.dumps(execute(json.load(sys.stdin)), sort_keys=True))
    except Exception:
        print('Target native stage refused; storage retained and private output withheld.', file=sys.stderr)
        sys.exit(1)
