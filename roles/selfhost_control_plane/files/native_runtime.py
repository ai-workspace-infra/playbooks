#!/usr/bin/env python3
"""Target-local fresh installation guard; domain SQL stays in native owners."""
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
            'accounts': contract['release']['accounts'], 'storage': contract['resolved']['data_disk']}


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
    checkpoint = native.sql('SELECT version::text||\':\'||dirty::text FROM public.schema_migrations', 'account')
    native.require(checkpoint == str(contract['release']['accounts']['migration_version']) + ':false',
                   'Native checkpoint changed or is dirty')
    identity = native.sql("SELECT system_identifier::text FROM pg_control_system()") + ':' + \
        native.sql("SELECT oid::text FROM pg_database WHERE datname='account'")
    native.require(record['database_identity'] == identity and record['schema_fingerprint'] == schema_fingerprint(),
                   'Initialized database identity or schema changed')
    return identity


def execute(request):
    contract = request['contract']
    action = request['action']
    native.require(action in ('preflight', 'claim', 'storage', 'initialize', 'verify'), 'Unknown native stage')
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
        # Reuse the existing domain permission owner; never duplicate grants or rotate passwords.
        if native.sql("SELECT count(*) FROM pg_roles WHERE rolname='account_user'") == '1':
            business.authenticate(request['business_password'])
        business.pg(business.permissions_sql(request['business_password']))
        business.authenticate(request['business_password'])
        record.update(initialized=True, schema_fingerprint=schema_fingerprint(),
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
