#!/usr/bin/env python3
"""Target-only native initialization. Never runs Accounts or reads a source DB."""
import argparse
from contextlib import contextmanager
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


def command(argv, timeout=360, **kwargs):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Native initialization command unavailable or timed out') from None
    # Commands and raw output can contain private database facts. Do not echo them.
    require(result.returncode == 0, 'Native initialization command failed; inspect privately')
    return result.stdout.strip()


def validate_credentials(credentials):
    require(isinstance(credentials, dict) and set(credentials) ==
            {'postgres_password', 'ghcr_username', 'ghcr_token'} and
            all(isinstance(v, str) and v and not any(c in v for c in '\r\n\x00')
                for v in credentials.values()) and
            re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', credentials['ghcr_username']),
            'Private target credentials are incomplete')


def read_credentials():
    # SSH-pipelined command stdin, never an argv/env/host file credential.
    raw = __import__('sys').stdin.readline(65537)
    require(len(raw.encode()) <= 65536 and raw.endswith('\n'),
            'Private target credential input is incomplete')
    credentials = json.loads(raw)
    validate_credentials(credentials)
    return credentials


def validate_registry_credentials(credentials):
    require(isinstance(credentials, dict) and set(credentials) == {'ghcr_username', 'ghcr_token'} and
            all(isinstance(v, str) and v and not any(c in v for c in '\r\n\x00')
                for v in credentials.values()) and
            re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', credentials['ghcr_username']),
            'Private registry credentials are incomplete')


@contextmanager
def registry_session(credentials):
    # Target tools retain their strict three-field contract. Runtime image
    # qualification never asks for or accepts a database password.
    if isinstance(credentials, dict) and 'postgres_password' in credentials:
        validate_credentials(credentials)
    else:
        validate_registry_credentials(credentials)
    require(command(['findmnt', '-nro', 'FSTYPE', '--target', '/dev/shm']) == 'tmpfs',
            'Registry authentication requires private volatile storage')
    with tempfile.TemporaryDirectory(prefix='native-registry-', dir='/dev/shm') as directory:
        Path(directory).chmod(0o700)
        env = dict(os.environ, DOCKER_CONFIG=directory)
        command(['docker', 'login', 'ghcr.io', '--username', credentials['ghcr_username'],
                 '--password-stdin'], input=credentials['ghcr_token'], env=env, timeout=60)
        yield env


TARGET_BOOTSTRAP = '''set -eu
IFS= read -r NATIVE_TARGET_DSN
export NATIVE_TARGET_DSN
exec /usr/local/bin/migratectl "$@"
'''


def tool_argv(image, name, args, migration_directory=None):
    argv = ['docker', 'run', '--rm', '--name', name, '-i', '--network',
            'container:web-saas-postgresql', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges']
    if migration_directory is not None:
        argv += ['--mount', 'type=bind,source=' + str(migration_directory) +
                 ',target=/reviewed-migrations,readonly']
    return argv + ['--entrypoint', '/bin/sh', image, '-c', TARGET_BOOTSTRAP, '--', *args]


def run_tool(image, name, args, credentials, env, migration_directory=None):
    target = 'postgresql://postgres:' + quote(credentials['postgres_password'], safe='') + \
             '@127.0.0.1:5432/account?sslmode=disable'
    try:
        return command(tool_argv(image, name, args, migration_directory),
                       input=target + '\n', env=env)
    finally:
        # A timed-out client may leave its container running with an open DB
        # connection. Explicitly end the owned execution before returning.
        remove_execution_container(name)


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


def execute(spec, dry_run, guard_directory, credentials):
    image = validate_spec(spec)
    require(type(dry_run) is bool and (dry_run or os.environ.get('NATIVE_DATA_GATE_VERIFIED') == 'true'),
            'Native initialization apply requires verified production data approval')
    validate_credentials(credentials)
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
    with registry_session(credentials) as env:
        command(['docker', 'pull', image], env=env)
        manifest = json.loads(command(['docker', 'run', '--rm', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--entrypoint', '/usr/local/bin/migratectl', image, 'native-schema'], env=env))
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
        if present == '0':
            # Creating a new absent DB is the only DDL outside migratectl's
            # transaction. Failure leaves an empty DB; it is never dropped/reset.
            command(['docker', 'exec', 'web-saas-postgresql', 'createdb', '-U', 'postgres',
                     '--template=template0', '--encoding=UTF8', 'account'])
        receipt = json.loads(run_tool(image, 'native-schema-init-' + uuid.uuid4().hex, ['init',
                '--dsn-env=NATIVE_TARGET_DSN', '--environment=prod',
                '--schema-sha256=' + spec['schema_sha256'], '--writers-paused',
                '--dry-run=' + str(dry_run).lower(), '--lock-timeout=15s', '--statement-timeout=5m'],
                credentials, env))
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
    receipt = execute(spec, args.dry_run == 'true', args.guard_directory, read_credentials())
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (Refused, ValueError, KeyError, TypeError, OSError):
        # Always sanitized: JSON/OS errors may include connection data or paths.
        print('Native schema initialization stopped; no source data or application service was accessed.',
              file=__import__('sys').stderr)
        raise SystemExit(1)
