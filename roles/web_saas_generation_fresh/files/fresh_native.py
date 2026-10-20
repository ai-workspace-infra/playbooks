#!/usr/bin/env python3
"""Vnext native initialization and migratectl transfer on the target host only."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

os.umask(0o077)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(argv, data=None, env=None):
    result = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    require(result.returncode == 0, 'target operation failed; inspect private execution evidence')
    return result.stdout


def sql(database, query):
    return run(['docker', 'exec', '-i', 'web-saas-postgresql', 'psql', '-U', 'postgres', '-d', database,
                '-XAtq', '-v', 'ON_ERROR_STOP=1'], query.encode()).decode().strip()


def save(root, name, value):
    path = root / (name + '.json')
    with path.open('x') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
    return value


def schema_digest(database):
    raw = run(['docker', 'exec', 'web-saas-postgresql', 'pg_dump', '-U', 'postgres', '-d', database,
               '--schema-only', '--no-owner', '--no-acl']).decode()
    normalized = '\n'.join(line for line in raw.splitlines()
                           if not line.startswith(('\\restrict ', '\\unrestrict ', '--')))
    return hashlib.sha256(normalized.encode()).hexdigest()


def rows_digest(database):
    tables = sql(database, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    digests = []
    for table in tables:
        require(re.fullmatch('[a-z][a-z0-9_]*', table) is not None, 'unexpected table name')
        digest = sql(database, "BEGIN READ ONLY; SET LOCAL timezone='UTC'; SET LOCAL datestyle='ISO,YMD'; SET LOCAL extra_float_digits=3; "
                     "SELECT md5(COALESCE(string_agg(h,'' ORDER BY h),'')) FROM (SELECT md5(row_to_json(t)::text) h FROM public.\"" + table + "\" t) q; COMMIT;")
        digests.append((table, digest))
    return hashlib.sha256(json.dumps(digests).encode()).hexdigest()


def permissions_digest(database):
    value = sql(database, "SELECT COALESCE(string_agg(c.relname||':'||c.relowner::text||':'||COALESCE(c.relacl::text,''),E'\\n' ORDER BY c.relname),'') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','S')")
    return hashlib.sha256(value.encode()).hexdigest()


def main(spec):
    generation, source, action = spec['target_generation'], spec['source_generation'], spec['action']
    require(re.fullmatch(r'prod-v[1-9][0-9]*', generation) is not None, 'invalid target generation')
    require(re.fullmatch(r'prod-v[1-9][0-9]*', source) is not None, 'invalid source generation')
    require(int(generation[6:]) == int(source[6:]) + 1, 'adjacent generations required')
    require(run(['hostname', '-s']).decode().strip() == 'web-saas-' + generation, 'target host mismatch')
    require(action in ['initialize', 'backup-restore', 'post-sync-restore', 'preview', 'align', 'verify'], 'unknown stage')
    root = Path(spec['checkpoint'])
    require(root.is_absolute() and not root.is_symlink() and
            str(root).startswith('/data/backups/web-saas/' + generation + '/fresh-native-'), 'invalid checkpoint')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / 'execution.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require(run(['findmnt', '-nro', 'TARGET', '--mountpoint', '/data']).decode().strip() == '/data', 'data disk missing')
    services = run(['docker', 'ps', '--format', '{{.Names}}']).decode().splitlines()
    require(not set(services) & {'web-saas-accounts', 'web-saas-billing', 'web-saas-console'}, 'target writers must be absent')
    binary = spec['container_binary']
    require(re.fullmatch(r'/tmp/migratectl-[a-zA-Z0-9_-]+', binary) is not None, 'invalid tool path')
    retained_binary = Path(spec['retained_binary'])
    require(str(retained_binary).startswith('/data/backups/web-saas/' + generation + '/') and
            hashlib.sha256(retained_binary.read_bytes()).hexdigest() == spec['binary_sha256'], 'retained tool digest mismatch')
    run(['docker', 'cp', str(retained_binary), 'web-saas-postgresql:' + binary])
    require(run(['docker', 'exec', 'web-saas-postgresql', 'sha256sum', binary]).decode().split()[0] ==
            spec['binary_sha256'], 'tool digest mismatch')
    source_db = spec['source_database']
    require(re.fullmatch(r'v[1-9][0-9]*_restore_[0-9]+', source_db) is not None, 'use a recorded isolated source snapshot')
    source_proof = json.loads(Path(spec['source_restore_receipt']).read_text())
    require(source_proof.get('restore') == 'passed' and source_proof.get('database') == source_db and
            source_proof.get('source_generation') == source and source_proof.get('target_generation') == generation and
            source_proof.get('archive_sha256') == spec['source_archive_sha256'], 'source restore evidence mismatch')
    require(sql('postgres', "SELECT oid FROM pg_database WHERE datname='" + source_db + "'") == source_proof['database_oid'],
            'isolated source database identity mismatch')
    target_dsn = 'postgresql://postgres@127.0.0.1:5432/account?sslmode=disable'
    source_dsn = 'postgresql://postgres@127.0.0.1:5432/' + source_db + '?sslmode=disable'

    def migrate(arguments, source_required=False):
        argv = ['docker', 'exec', '-e', 'TARGET_DSN=' + target_dsn]
        if source_required:
            argv += ['-e', 'SOURCE_DSN=' + source_dsn]
        result = run(argv + ['web-saas-postgresql', binary] + arguments)
        return json.loads(result)

    native = spec['schema_sha256']
    billing = spec['billing_sha256']
    receipt = root / ('verify-full.json' if action == 'verify' else action + '.json')
    if receipt.exists():
        old = json.loads(receipt.read_text())
        require(old.get('target_generation') == generation and old.get('source_generation') == source and
                old.get('source_sha') == spec['source_sha'], 'checkpoint belongs to another operation')
        if action != 'verify':
            initial = old if action == 'initialize' else json.loads((root / 'initialize.json').read_text())
            require(sql('postgres', "SELECT oid FROM pg_database WHERE datname='account'") == initial['target_database_oid'],
                    'replay target identity changed')
            require(schema_digest('account') == initial['schema_fingerprint'], 'replay schema drift')
            if action in ['backup-restore', 'post-sync-restore']:
                archive = root / ('account.after-sync.dump.enc' if action == 'post-sync-restore' else 'account.before-sync.dump.enc')
                require(hashlib.sha256(archive.read_bytes()).hexdigest() == old['archive_sha256'], 'recorded archive changed')
                restored = old['isolated_restore_database']
                require(re.fullmatch(r'fresh_(after_)?verify_[0-9]+', restored) is not None, 'invalid recorded restore identity')
                require(schema_digest(restored) == old['restored_schema_sha256'], 'restored schema drift')
                if 'restored_rows_sha256' in old:
                    require(rows_digest(restored) == old['restored_rows_sha256'], 'restored business rows drift')
                    require(permissions_digest(restored) == old['permissions_sha256'], 'restored permissions drift')
            if action in ['preview', 'align']:
                comparison = migrate(['compare-full-business', '--source-dsn-env', 'SOURCE_DSN', '--target-dsn-env', 'TARGET_DSN',
                                      '--environment', 'prod', '--writers-paused', '--schema-sha256', native,
                                      '--billing-schema-sha256', billing, '--dry-run=false'], True)
                require(comparison.get('full_business_equal') is True and comparison.get('target_writes') is False,
                        'recorded synchronization no longer matches target')
            return dict(status='revalidated', action=action, checkpoint=str(receipt), target_writes=False)
    result = {'action': action, 'target_generation': generation, 'source_generation': source,
              'source_sha': spec['source_sha'], 'schema_sha256': native,
              'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'source_snapshot_archive_sha256': spec['source_archive_sha256'],
              'source_snapshot_database': source_db, 'public_dns_cutover': False}
    if action == 'initialize':
        require(sql('postgres', "SELECT count(*) FROM pg_database WHERE datname='account'") == '0',
                'account already exists; initialization never resets a target')
        save(root, 'before-create', dict(result, account_absent=True))
        run(['docker', 'exec', 'web-saas-postgresql', 'createdb', '-U', 'postgres', 'account'])
        flags = ['init', '--dsn-env', 'TARGET_DSN', '--environment', 'prod', '--writers-paused', '--schema-sha256', native]
        save(root, 'native-init-preview', migrate(flags))
        result['native_init'] = migrate(flags + ['--dry-run=false'])
        billing_file = Path(spec['billing_sql'])
        require(hashlib.sha256(billing_file.read_bytes()).hexdigest() == billing, 'Billing SQL checksum mismatch')
        sql('account', 'BEGIN;\n' + billing_file.read_text() + '\nCOMMIT;')
        require(sql('account', 'SELECT count(*) FROM users') == '0', 'native initialization seeded users')
        result['target_database_oid'] = sql('postgres', "SELECT oid FROM pg_database WHERE datname='account'")
        result['schema_fingerprint'] = schema_digest('account')
    else:
        require((root / 'initialize.json').exists(), 'native initialization proof missing')
        initial = json.loads((root / 'initialize.json').read_text())
        require(sql('postgres', "SELECT oid FROM pg_database WHERE datname='account'") == initial['target_database_oid'],
                'target database identity changed')
        require(sql('account', "SELECT version::text||':'||dirty::text FROM schema_migrations") == str(spec['schema_version']) + ':false',
                'target schema checkpoint differs')
        if action in ['backup-restore', 'post-sync-restore']:
            key = spec['backup_key']
            require(len(key) >= 32, 'backup key missing')
            archive = root / ('account.after-sync.dump.enc' if action == 'post-sync-restore' else 'account.before-sync.dump.enc')
            before_rows = rows_digest('account')
            before_permissions = permissions_digest('account')
            environment = dict(os.environ, GENERATION_BACKUP_KEY=key)
            dump = subprocess.Popen(['docker', 'exec', 'web-saas-postgresql', 'pg_dump', '-U', 'postgres', '-d', 'account', '-Fc'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with archive.open('xb') as handle:
                encrypted = subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-iter', '200000', '-pass', 'env:GENERATION_BACKUP_KEY'], stdin=dump.stdout, stdout=handle, stderr=subprocess.PIPE, env=environment)
            dump.stdout.close()
            require(dump.wait() == 0 and encrypted.returncode == 0, 'target backup failed')
            verify_db = ('fresh_after_verify_' if action == 'post-sync-restore' else 'fresh_verify_') + re.sub(r'[^0-9]', '', root.name)[-14:]
            require(sql('postgres', "SELECT count(*) FROM pg_database WHERE datname='" + verify_db + "'") == '0', 'restore database already exists')
            run(['docker', 'exec', 'web-saas-postgresql', 'createdb', '-U', 'postgres', verify_db])
            decrypt = subprocess.Popen(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-pass', 'env:GENERATION_BACKUP_KEY', '-in', str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            restored = subprocess.run(['docker', 'exec', '-i', 'web-saas-postgresql', 'pg_restore', '-U', 'postgres', '-d', verify_db, '--no-owner', '--exit-on-error', '--single-transaction'], stdin=decrypt.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            decrypt.stdout.close()
            require(decrypt.wait() == 0 and restored.returncode == 0, 'target isolated restore failed; database retained')
            require(schema_digest('account') == schema_digest(verify_db), 'target restored schema mismatch')
            restored_rows = rows_digest(verify_db)
            restored_permissions = permissions_digest(verify_db)
            require(before_rows == restored_rows, 'restored business rows mismatch')
            require(before_permissions == restored_permissions, 'restored table and sequence permissions mismatch')
            require(sql(verify_db, 'SELECT count(*) FROM users') == sql('account', 'SELECT count(*) FROM users'), 'restored user count mismatch')
            if action == 'post-sync-restore':
                count = run(['docker', 'exec', 'web-saas-postgresql', 'psql', '-U', 'account_user', '-d', verify_db,
                             '-XAtq', '-c', 'SELECT count(*) FROM public.users']).decode().strip()
                require(count == sql('account', 'SELECT count(*) FROM users'), 'application role cannot read the restored business data')
            result.update(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), isolated_restore_database=verify_db,
                          restored_schema_sha256=schema_digest(verify_db), source_rows_sha256=before_rows,
                          restored_rows_sha256=restored_rows, permissions_sha256=restored_permissions,
                          business_data_populated=action == 'post-sync-restore', result='passed')
        else:
            require((root / 'backup-restore.json').exists(), 'independent target backup/restore proof missing')
            arguments = ['compare-full-business' if action == 'verify' else 'copy-full-business',
                         '--source-dsn-env', 'SOURCE_DSN', '--target-dsn-env', 'TARGET_DSN',
                         '--environment', 'prod', '--writers-paused', '--schema-sha256', native, '--billing-schema-sha256', billing]
            if action == 'align':
                require((root / 'preview.json').exists(), 'read-only preview proof missing')
                arguments += ['--dry-run=false']
            elif action == 'verify':
                arguments += ['--dry-run=false']
            result['business'] = migrate(arguments, True)
            if action == 'verify':
                require(result['business'].get('full_business_equal') is True and
                        result['business'].get('target_writes') is False, 'full business comparison did not prove equality')
            require(schema_digest('account') == initial['schema_fingerprint'], 'data synchronization changed native schema')
    if receipt.exists():
        require(action == 'verify', 'completed stage cannot be overwritten')
        return result
    save(root, receipt.stem, result)
    return result


if __name__ == '__main__':
    try:
        value = main(json.load(sys.stdin))
        print(json.dumps(value))
    except Exception as error:
        # Error messages are controlled; never echo a DSN, credentials or SQL.
        print(json.dumps({'status': 'stopped', 'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__}))
        sys.exit(1)
