#!/usr/bin/env python3
"""Billing-owned additive schema before any business copy; no source access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import quote
import native_init_host as native


def validate_spec(spec, migration):
    image = native.validate_spec(spec['initialization'])
    native.require(spec['initialization']['business_table_count'] == 52, 'Accounts native scope must contain 52 tables')
    contract = spec['billing']
    native.require(re.fullmatch(r'[0-9a-f]{40}', contract.get('commit', '')) is not None,
                   'Billing requires a fixed full source SHA')
    native.require(contract.get('owner') == 'ai-workspace-services/billing-service' and
        contract.get('migration_file') == 'sql/migrations/2026100701_cloud_vendor_costs.up.sql' and
        contract.get('business_tables') == ['cloud_vendor_costs'] and
        contract.get('expected_schema_version') == spec['initialization']['migration_version'] == 2026100601 and
        contract.get('target_schema_version') == 2026100701 and
        contract.get('no_business_seeds') is True and contract.get('database_cutover_approved') is False,
        'Billing additive schema scope/version differs')
    native.require(migration.name == '2026100701_cloud_vendor_costs.up.sql' and
        not migration.is_symlink() and migration.is_file() and
        re.fullmatch(r'[0-9a-f]{64}', contract.get('migration_sha256', '')) is not None and
        hashlib.sha256(migration.read_bytes()).hexdigest() == contract['migration_sha256'],
        'Billing SQL bytes differ from the reviewed digest')
    return image


def verify_target(spec, version):
    tables = spec['initialization']['business_tables'] + (['cloud_vendor_costs'] if version == 2026100701 else [])
    tables = sorted(tables)
    actual = native.sql("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_migrations'", 'account')
    native.require(json.loads(actual) == tables, 'Target business table scope differs; refusing upgrade')
    state = native.sql("SELECT version::text || ':' || dirty::text FROM public.schema_migrations", 'account')
    native.require(state == str(version) + ':false', 'Target migration version is not the reviewed clean state')
    # Identifier scope was validated above. Count all business rows, including
    # newly added Billing data; this operation is only for the empty migration target.
    rows = native.sql('SELECT ' + ' + '.join('(SELECT count(*) FROM public."' + t + '")' for t in tables), 'account')
    native.require(rows == '0', 'Business rows exist; this pre-copy schema stage cannot proceed')
    return tables


def verify_storage():
    native.require(native.command(['findmnt', '-nro', 'TARGET,FSTYPE', '--mountpoint', '/data']) == '/data ext4',
                   'Independent PostgreSQL mount differs')
    source = native.command(['findmnt', '-nro', 'SOURCE', '--mountpoint', '/data'])
    native.require(native.command(['readlink', '-f', source]) == native.command([
        'readlink', '-f', '/dev/disk/by-id/google-web-saas-prod-data']), 'Independent disk identity differs')
    native.require(native.command(['docker', 'inspect', '-f',
        '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}}:{{.Source}}{{end}}{{end}}',
        'web-saas-postgresql']) == 'bind:/data/postgresql', 'PostgreSQL data bind differs')
    native.require(native.command(['docker', 'inspect', '-f', '{{.State.Status}}', 'web-saas-postgresql']) == 'running',
                   'PostgreSQL is not running')
    native.require(re.fullmatch(r'17[0-9]{4}', native.sql('SHOW server_version_num')) is not None,
                   'Billing native schema requires qualified PostgreSQL 17')


def execute(spec, migration, guard_directory, dry_run):
    image = validate_spec(spec, migration)
    native.command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
    verify_storage()
    version = native.sql('SELECT version FROM public.schema_migrations WHERE dirty=false', 'account')
    native.require(version in ('2026100601', '2026100701'), 'Target version is unqualified or dirty')
    version = int(version)
    initial_version = version
    verify_target(spec, version)
    native.command(['docker', 'pull', image])
    manifest = json.loads(native.command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/migratectl',
        image, 'native-schema']))
    native.validate_manifest(manifest, spec['initialization'])
    if not dry_run and version == 2026100601:
        native.require(os.environ.get('NATIVE_DATA_GATE_VERIFIED') == 'true', 'Independent PROD approval is required')
        password = os.environ.get('NATIVE_POSTGRES_PASSWORD', '')
        native.require(password and '\n' not in password and '\r' not in password, 'Target runtime credential unavailable')
        native.command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
        verify_storage()
        verify_target(spec, 2026100601)
        with tempfile.TemporaryDirectory(prefix='native-billing-') as directory:
            private = Path(directory)
            env_file = private / 'target.env'
            env_file.write_text('NATIVE_TARGET_DSN=postgresql://postgres:' + quote(password, safe='') +
                               '@127.0.0.1:5432/account?sslmode=disable\n')
            env_file.chmod(0o600)
            sql_dir = private / 'migrations'
            sql_dir.mkdir(mode=0o700)
            (sql_dir / migration.name).write_bytes(migration.read_bytes())
            (sql_dir / migration.name).chmod(0o600)
            execution_name = 'native-billing-upgrade-' + uuid.uuid4().hex
            try:
                native.command(['docker', 'run', '--rm', '--name', execution_name,
                    '--network', 'container:web-saas-postgresql', '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--env-file', str(env_file),
                    '--mount', 'type=bind,source=' + str(sql_dir) + ',target=/reviewed-migrations,readonly',
                    '--entrypoint', '/usr/local/bin/migratectl', image, '--dir=/reviewed-migrations', 'migrate',
                    '--dsn-env=NATIVE_TARGET_DSN', '--expected-version=2026100601', '--target-version=2026100701',
                    '--migration-sha256=' + spec['billing']['migration_sha256'],
                    '--lock-timeout=15s', '--statement-timeout=5m'])
            finally:
                native.remove_execution_container(execution_name)
        version = 2026100701
    tables = verify_target(spec, version)
    native.command(['bash', str(guard_directory / 'native_writer_guard_host.sh')])
    return {'stage': 'native_billing_schema_preview' if dry_run else 'native_billing_schema_upgraded',
        'result': 'eligible' if dry_run else 'upgraded', 'environment': 'prod', 'host': 'web-saas-prod',
        'database': 'account', 'migration_version': version, 'target_version': 2026100701,
        'billing_commit': spec['billing']['commit'], 'migration_sha256': spec['billing']['migration_sha256'],
        'accounts_commit': spec['initialization']['accounts_commit'],
        'image_digest': spec['initialization']['image_digest'], 'business_tables': tables,
        'business_rows': 0, 'writers_paused': True, 'independent_disk_verified': True,
        'database_cutover_approved': False, 'schema_changed': not dry_run and initial_version == 2026100601}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--migration', type=Path, required=True)
    parser.add_argument('--guard-directory', type=Path, required=True)
    parser.add_argument('--dry-run', choices=('true', 'false'), default='true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    native.require(args.dry_run == 'true' or os.environ.get('NATIVE_DATA_GATE_VERIFIED') == 'true',
                   'Billing schema apply requires independent PROD approval')
    spec = json.loads(args.spec.read_text())
    validate_spec(spec, args.migration)
    if args.validate_only:
        print('Fixed Billing additive schema contract verified; no database action performed.')
        return
    print(json.dumps(execute(spec, args.migration, args.guard_directory, args.dry_run == 'true'), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (native.Refused, ValueError, KeyError, TypeError, OSError):
        print('Native Billing schema stage stopped; private command output withheld.', file=__import__('sys').stderr)
        raise SystemExit(1)
