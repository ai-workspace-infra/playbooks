#!/usr/bin/env python3
"""Target-only native initialization. Never runs Accounts or reads a source DB."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from urllib.parse import quote


class Refused(Exception):
    pass


def require(value, message):
    if not value:
        raise Refused(message)


def command(argv, **kwargs):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=360, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Native initialization command unavailable or timed out') from None
    # Commands and raw output can contain private database facts. Do not echo them.
    require(result.returncode == 0, 'Native initialization command failed; inspect privately')
    return result.stdout.strip()


def validate_spec(spec):
    require(spec.get('schema') == 1 and spec.get('environment') == 'prod' and
            spec.get('host') == 'web-saas-prod' and spec.get('database') == 'account',
            'Native initialization target contract differs')
    require(re.fullmatch(r'[0-9a-f]{40}', spec.get('accounts_commit', '')) is not None,
            'Native initialization requires full Accounts source SHA')
    require(spec.get('image') == 'ghcr.io/ai-workspace-services/accounts:sha-' + spec['accounts_commit'],
            'Accounts initialization image differs from the reviewed source')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', spec.get('image_digest', '')) is not None and
            re.fullmatch(r'[0-9a-f]{64}', spec.get('schema_sha256', '')) is not None,
            'Native initialization requires exact image and SQL digests')
    tables = spec.get('business_tables')
    require(isinstance(tables, list) and len(tables) == spec.get('business_table_count') and
            len(tables) > 0 and tables == sorted(set(tables)) and
            all(isinstance(t, str) and re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tables),
            'Native initialization business scope is invalid')
    require(type(spec.get('migration_version')) is int and spec['migration_version'] > 0,
            'Native initialization version is invalid')
    return spec['image'] + '@' + spec['image_digest']


def validate_manifest(manifest, spec):
    require(manifest.get('format') == 1 and all(manifest.get(key) == spec[key] for key in
            ('schema_sha256', 'migration_version', 'business_table_count', 'business_tables')),
            'Compiled native schema manifest differs from the reviewed contract')


def validate_receipt(receipt, spec, dry_run):
    require(receipt.get('result') == ('eligible' if dry_run else 'initialized') and
            receipt.get('environment') == 'prod' and receipt.get('database') == 'account' and
            receipt.get('schema_sha256') == spec['schema_sha256'] and
            receipt.get('migration_version') == spec['migration_version'] and
            receipt.get('business_tables') == spec['business_tables'] and
            receipt.get('business_rows') == 0 and
            receipt.get('database_cutover_approved') is False,
            'Native initialization receipt differs from the reviewed scope')


def sql(query, database='postgres'):
    return command(['docker', 'exec', 'web-saas-postgresql', 'psql', '-U', 'postgres',
                    '-d', database, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-c', query])


def remove_execution_container(name):
    try:
        result = subprocess.run(['docker', 'rm', '--force', name], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Cannot verify native execution container cleanup') from None
    absent = result.stderr.strip().lower() in (
        'error: no such container: ' + name,
        'error response from daemon: no such container: ' + name)
    require(result.returncode == 0 or result.returncode == 1 and absent,
            'Cannot verify native execution container cleanup')


def execute(spec, dry_run, guard_directory):
    image = validate_spec(spec)
    # These existing guards inspect real independent storage, empty DB and all
    # managed application/reconciler containers. No service is stopped silently.
    command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
    command(['bash', str(guard_directory / 'init_guard_host.sh'), 'prod'])
    require(re.fullmatch(r'17[0-9]{4}', sql('SHOW server_version_num')) is not None,
            'Native schema requires qualified PostgreSQL 17')
    present = sql("SELECT count(*) FROM pg_database WHERE datname='account'")
    require(present in ('0', '1'), 'Cannot establish target database existence')
    # Pull only the prebuilt digest. Overriding ENTRYPOINT prevents RunServer,
    # application seeds, schedulers, proxy rotation, or schema auto-migration.
    command(['docker', 'pull', image])
    manifest = json.loads(command(['docker', 'run', '--rm', '--network', 'none',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--entrypoint', '/usr/local/bin/migratectl', image, 'native-schema']))
    validate_manifest(manifest, spec)
    command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
    command(['bash', str(guard_directory / 'init_guard_host.sh'), 'prod'])
    if present == '0' and dry_run:
        return {'stage': 'native_schema_preview', 'result': 'eligible_absent_database',
                'environment': 'prod', 'host': 'web-saas-prod', 'database': 'account',
                'schema_sha256': spec['schema_sha256'], 'migration_version': spec['migration_version'],
                'business_tables': spec['business_tables'], 'business_rows': 0,
                'schema_initialized': False, 'database_created': False,
                'writers_paused': True, 'database_cutover_approved': False,
                'accounts_commit': spec['accounts_commit'], 'image_digest': spec['image_digest'],
                'independent_disk_verified': True}
    password = os.environ.get('NATIVE_POSTGRES_PASSWORD', '')
    require(password and '\n' not in password and '\r' not in password,
            'Target PostgreSQL runtime credential is unavailable')
    with tempfile.TemporaryDirectory(prefix='native-init-') as directory:
        env_file = Path(directory) / 'target.env'
        env_file.write_text('NATIVE_TARGET_DSN=postgresql://postgres:' + quote(password, safe='') +
                            '@127.0.0.1:5432/account?sslmode=disable\n')
        env_file.chmod(0o600)
        if present == '0':
            # Creating a new absent DB is the only DDL outside migratectl's
            # transaction. Failure leaves an empty DB; it is never dropped/reset.
            command(['docker', 'exec', 'web-saas-postgresql', 'createdb', '-U', 'postgres',
                     '--template=template0', '--encoding=UTF8', 'account'])
        execution_name = 'native-schema-init-' + uuid.uuid4().hex
        try:
            receipt = json.loads(command(['docker', 'run', '--rm', '--name', execution_name, '--network',
                'container:web-saas-postgresql', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--env-file', str(env_file),
                '--entrypoint', '/usr/local/bin/migratectl', image, 'init',
                '--dsn-env=NATIVE_TARGET_DSN', '--environment=prod',
                '--schema-sha256=' + spec['schema_sha256'], '--writers-paused',
                '--dry-run=' + str(dry_run).lower(), '--lock-timeout=15s', '--statement-timeout=5m']))
        finally:
            # Docker client timeout does not reliably stop its container. End
            # the owned execution before removing its private credentials.
            remove_execution_container(execution_name)
    validate_receipt(receipt, spec, dry_run)
    command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
    receipt.update(stage='native_schema_preview' if dry_run else 'native_schema_initialized',
        host='web-saas-prod', writers_paused=True, schema_initialized=not dry_run,
        database_created=present == '0', accounts_commit=spec['accounts_commit'],
        image_digest=spec['image_digest'], independent_disk_verified=True)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--guard-directory', type=Path, required=True)
    parser.add_argument('--dry-run', choices=('true', 'false'), default='true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    # Apply is allowed only behind Toolkit's independent production data gate.
    # This is an owner assertion, not a substitute for that control-plane check.
    require(args.dry_run == 'true' or os.environ.get('NATIVE_DATA_GATE_VERIFIED') == 'true',
            'Native initialization apply requires verified production data approval')
    spec = json.loads(args.spec.read_text())
    validate_spec(spec)
    if args.validate_only:
        print('Reviewed native schema contract is valid; no host/database action performed.')
        return
    receipt = execute(spec, args.dry_run == 'true', args.guard_directory)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (Refused, ValueError, KeyError, TypeError, OSError):
        # Always sanitized: JSON/OS errors may include connection data or paths.
        print('Native schema initialization stopped; no source data or application service was accessed.',
              file=__import__('sys').stderr)
        raise SystemExit(1)
