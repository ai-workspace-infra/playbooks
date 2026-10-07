#!/usr/bin/env python3
"""Playbooks database owner. Toolkit owns approval/evidence; IaC owns access.

One reviewed prebuilt migratectl operation on canonical CMDB target. Source is
readonly; no data dump, schema replay, reset, application startup or DNS action.
"""
import argparse
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
import uuid

import native_init_host as native
from native_billing_upgrade_host import verify_storage

ACCOUNTS_SQL_SHA256 = '842cef3beb98ef819dc854ecdf5f85683233641a0cd85a9156b30ad59f7e0206'
BILLING_SQL_SHA256 = 'a7133f3ef2ea9013a055cfd1442a7488d2b837f289e0f5d9b61624d4fde9bc53'
FAILURE_STAGES = ('input', 'writer_guard', 'storage', 'target_schema', 'registry_storage',
    'registry_login', 'image_pull', 'image_manifest', 'target_recheck', 'migration',
    'receipt', 'final_guard')
CURRENT_STAGE = 'input'


def stage(name):
    global CURRENT_STAGE
    native.require(name in FAILURE_STAGES, 'Invalid diagnostic stage')
    CURRENT_STAGE = name


def failure_receipt():
    # Fixed vocabulary only: never exception text, command arguments or DB rows.
    return {'format': 1, 'result': 'failed', 'failure_stage': CURRENT_STAGE,
        'database_cutover_approved': False}


def validate_spec(spec):
    native.validate_spec(spec['initialization'])
    initial = spec['initialization']
    native.require(initial['migration_version'] == 2026100601 and initial['business_table_count'] == 52 and
        initial['schema_sha256'] == ACCOUNTS_SQL_SHA256, 'Unqualified Accounts native boundary')
    transfer = spec['transfer']
    native.require(transfer.get('schema') == 1 and transfer.get('environment') == 'prod' and
        transfer.get('host') == 'web-saas-prod' and transfer.get('database') == 'account' and
        re.fullmatch('[0-9a-f]{40}', transfer.get('accounts_commit', '')) and
        transfer.get('image') == 'ghcr.io/ai-workspace-services/accounts:sha-' + transfer['accounts_commit'] and
        re.fullmatch('sha256:[0-9a-f]{64}', transfer.get('image_digest', '')) and
        transfer.get('schema_sha256') == initial['schema_sha256'] and
        transfer.get('billing_schema_sha256') == BILLING_SQL_SHA256 and
        transfer.get('migration_version') == 2026100701 and
        transfer.get('business_tables') == sorted(initial['business_tables'] + ['cloud_vendor_costs']) and
        transfer.get('batch_size') == 1000 and transfer.get('database_cutover_approved') is False,
        'Full-business image/schema/table scope differs')
    source = spec['source']
    identity = source.get('identity_sha256')
    native.require(source.get('role') == 'serverless_supabase' and source.get('endpoint') in ('direct', 'session_pooler') and
        source.get('tls_required') is True and
        re.fullmatch('[a-z0-9]{20}', source.get('project_ref', '')) and
        type(source.get('ready')) is bool and (identity is None or re.fullmatch('[0-9a-f]{64}', identity)) and
        source.get('direction') == 'prod-supabase-to-prod-selfhost',
        'Source must use the explicit PROD-to-Selfhost readonly session-pooler contract')
    return transfer['image'] + '@' + transfer['image_digest']


def validate_source_dsn(dsn, source):
    native.require(isinstance(dsn, str) and dsn and not any(c in dsn for c in '\r\n\x00'),
        'Source credential transport is invalid')
    parsed = urlsplit(dsn)
    login = unquote(parsed.username or '')
    endpoint = source['endpoint']
    if endpoint == 'direct':
        host_ok = parsed.hostname == 'db.' + source['project_ref'] + '.supabase.co'
        login_ok = login in ('postgres', 'readonly_release')
    else:
        host_ok = re.fullmatch(r'aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com', parsed.hostname or '') is not None
        login_ok = re.fullmatch(r'(?:postgres|readonly_release)\.' + re.escape(source['project_ref']), login) is not None
    native.require(parsed.scheme in ('postgres', 'postgresql') and login_ok and parsed.port == 5432 and host_ok and
        parsed.path == '/postgres' and parsed.password,
        'Source must match the pinned PROD Supabase endpoint and project')
    # Vault may store the direct URI without an sslmode query. Force TLS before
    # passing the DSN to migratectl; explicit weaker/duplicate modes are refused.
    query = parse_qsl(parsed.query, keep_blank_values=True)
    ssl_modes = [value for key, value in query if key == 'sslmode']
    native.require(not ssl_modes or (len(ssl_modes) == 1 and ssl_modes[0] in
        ('require', 'verify-ca', 'verify-full')), 'Source connection must require TLS')
    if not ssl_modes:
        query.append(('sslmode', 'require'))
    # The existing Serverless login may be an admin; its migration connection
    # must default to readonly before any transaction is opened. The tool also
    # explicitly starts a readonly repeatable-read transaction.
    readonly_modes = [value for key, value in query if key == 'default_transaction_read_only']
    native.require(not readonly_modes or readonly_modes == ['on'],
        'Source connection must default to readonly')
    native.require(not any(key == 'options' for key, _ in query),
        'Source startup options may not override readonly defaults')
    if not readonly_modes:
        query.append(('default_transaction_read_only', 'on'))
    secure_dsn = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    # Must match migratectl's Go struct JSON, including pooler project login.
    identity = {'Host': parsed.hostname, 'Port': parsed.port, 'Database': 'postgres', 'Role': login}
    digest = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
    expected = source.get('identity_sha256')
    native.require(expected is None or digest == expected, 'Source identity differs from approved connection contract')
    return digest, secure_dsn


def validate_credentials(credentials, spec):
    native.require(isinstance(credentials, dict) and set(credentials) ==
        {'postgres_password', 'source_dsn', 'ghcr_username', 'ghcr_token'} and
        all(isinstance(v, str) and v and not any(c in v for c in '\r\n\x00') for v in credentials.values()) and
        re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', credentials['ghcr_username']),
        'Private runtime credentials are incomplete')
    identity, secure_dsn = validate_source_dsn(credentials['source_dsn'], spec['source'])
    credentials['source_dsn'] = secure_dsn
    return identity


def verify_target(spec, require_empty):
    transfer = spec['transfer']
    native.require(native.sql("SELECT version::text || ':' || dirty::text FROM public.schema_migrations", 'account') ==
        '2026100701:false', 'Native Billing checkpoint is not clean/exact')
    actual = json.loads(native.sql("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') FROM pg_tables "
        "WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','system_release_checkpoints')", 'account'))
    native.require(actual == transfer['business_tables'], 'Actual full-business target scope differs')
    if require_empty:
        count = native.sql('SELECT ' + ' + '.join('(SELECT count(*) FROM public."' + t + '")'
            for t in transfer['business_tables']), 'account')
        native.require(count == '0', 'Populated target refused; baseline copy never resets or upserts')


def run_private(argv, input=None, env=None, timeout=1860):
    # Capture only sanitized migratectl receipt. Raw stderr/argv may contain
    # connection facts; never expose a subprocess exception or raw stream.
    try:
        result = subprocess.run(argv, input=input, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise native.Refused('Full-business command unavailable or timed out') from None
    native.require(result.returncode == 0 and len(result.stdout.encode()) <= 65536,
        'Full-business command failed; private output withheld')
    return result.stdout.strip()


def validate_receipt(receipt, spec, mode, source_identity_sha256):
    expected = spec['transfer']
    native.require(receipt.get('format') == 1 and receipt.get('environment') == 'prod' and
        receipt.get('schema_sha256') == expected['schema_sha256'] and
        receipt.get('billing_schema_sha256') == expected['billing_schema_sha256'] and
        receipt.get('migration_version') == 2026100701 and receipt.get('batch_size') == 1000 and
        receipt.get('source_identity_sha256') == source_identity_sha256 and
        all(re.fullmatch('[0-9a-f]{64}', receipt.get(key, '')) for key in
            ('source_snapshot_sha256', 'source_catalog_sha256')) and
        receipt.get('source_read_only') is True and receipt.get('database_cutover_approved') is False and
        type(receipt.get('source_table_count')) is int and 44 <= receipt['source_table_count'] <= 53 and
        type(receipt.get('user_count')) is int and receipt['user_count'] > 0,
        'Full-business receipt identity/scope differs')
    native.require(receipt.get('result') == {'preview': 'eligible', 'copy': 'copied', 'compare': 'equal'}[mode] and
        receipt.get('target_writes') is (mode == 'copy') and
        receipt.get('full_business_equal') is (mode != 'preview'), 'Preview/copy/equality receipt mode differs')
    tables = receipt.get('tables')
    native.require(isinstance(tables, dict), 'Missing per-table business evidence')
    if mode == 'preview':
        native.require(tables == {}, 'Preview must not claim row equality')
    else:
        native.require(sorted(tables) == expected['business_tables'], 'Equality must cover all 53 business tables')
        for proof in tables.values():
            native.require(isinstance(proof, dict) and set(proof) == {'rows', 'sha256'} and
                type(proof['rows']) is int and proof['rows'] >= 0 and
                re.fullmatch('[0-9a-f]{64}', proof['sha256']), 'Invalid per-table row/digest proof')
        native.require(tables['users']['rows'] == receipt['user_count'], 'User population proof differs')
    # Reconstruct rather than republish arbitrary tool output. No row strings,
    # emails, Proxy UUIDs, DSNs, stdout extras or private connection metadata.
    safe = {key: receipt[key] for key in ('format', 'result', 'environment', 'schema_sha256',
        'billing_schema_sha256', 'migration_version', 'batch_size', 'source_table_count', 'user_count',
        'source_identity_sha256', 'source_snapshot_sha256', 'source_catalog_sha256', 'source_read_only',
        'full_business_equal', 'target_writes', 'database_cutover_approved')}
    times=[]
    for key in ('snapshot_started_at', 'completed_at'):
        value=receipt.get(key)
        native.require(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',value),
            'Source snapshot timestamps must be UTC RFC3339')
        at=datetime.fromisoformat(value.replace('Z','+00:00'));times.append(at)
        safe[key]=value
    native.require(0 <= (times[1]-times[0]).total_seconds() <= 1800,
        'Full-business snapshot exceeds reviewed transaction budget')
    safe['tables'] = tables
    return safe


# Secrets arrive via the SSH-pipelined module stdin and container stdin. They
# never appear in Docker's persistent environment config or a host env file.
CONTAINER_BOOTSTRAP = '''set -eu
IFS= read -r NATIVE_SOURCE_DSN
IFS= read -r NATIVE_TARGET_DSN
export NATIVE_SOURCE_DSN NATIVE_TARGET_DSN
exec /usr/local/bin/migratectl "$@"
'''


def migration_args(spec, mode):
    native.require(mode in ('preview','copy','compare'), 'Invalid full-business operation')
    operation = 'compare-full-business' if mode == 'compare' else 'copy-full-business'
    return [operation, '--source-dsn-env=NATIVE_SOURCE_DSN', '--target-dsn-env=NATIVE_TARGET_DSN',
        '--environment=prod', '--schema-sha256=' + spec['transfer']['schema_sha256'],
        '--billing-schema-sha256=' + spec['transfer']['billing_schema_sha256'], '--writers-paused',
        '--dry-run=' + str(mode == 'preview').lower()]


def execute(spec, guard_directory, mode, credentials):
    stage('input')
    image = validate_spec(spec)
    native.require(mode in ('preview', 'copy', 'compare') and
        os.environ.get('NATIVE_DATA_GATE_VERIFIED') == 'true', 'Independent production data review is required')
    source_identity_sha256 = validate_credentials(credentials, spec)
    guard = ['bash', str(guard_directory / 'native_writer_guard_host.sh')]
    stage('writer_guard')
    native.command(guard)
    stage('storage')
    verify_storage()
    stage('target_schema')
    verify_target(spec, require_empty=mode != 'compare')
    stage('registry_storage')
    native.require(native.command(['findmnt', '-nro', 'FSTYPE', '--target', '/dev/shm']) == 'tmpfs',
        'Registry authentication requires private volatile storage')
    with tempfile.TemporaryDirectory(prefix='full-business-registry-', dir='/dev/shm') as directory:
        Path(directory).chmod(0o700)
        env = dict(os.environ, DOCKER_CONFIG=directory)
        stage('registry_login')
        run_private(['docker', 'login', 'ghcr.io', '--username', credentials['ghcr_username'], '--password-stdin'],
            input=credentials['ghcr_token'], env=env, timeout=60)
        stage('image_pull')
        run_private(['docker', 'pull', image], env=env, timeout=360)
        stage('image_manifest')
        manifest = json.loads(run_private(['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/migratectl',
            image, 'native-schema'], env=env, timeout=60))
        native.validate_manifest(manifest, spec['initialization'])
        stage('target_recheck')
        native.command(guard)
        verify_storage()
        verify_target(spec, require_empty=mode != 'compare')
        target = 'postgresql://postgres:' + quote(credentials['postgres_password'], safe='') + \
            '@127.0.0.1:5432/account?sslmode=disable'
        execution_name = 'full-business-transfer-' + uuid.uuid4().hex
        args = migration_args(spec,mode)
        stage('migration')
        try:
            raw = run_private(['docker', 'run', '--rm', '--name', execution_name, '-i', '--network',
                'container:web-saas-postgresql', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--entrypoint', '/bin/sh', image,
                '-c', CONTAINER_BOOTSTRAP, '--', *args], input=credentials['source_dsn'] + '\n' + target + '\n', env=env)
        finally:
            native.remove_execution_container(execution_name)
        stage('receipt')
        receipt = validate_receipt(json.loads(raw), spec, mode, source_identity_sha256)
    stage('final_guard')
    native.command(guard)
    verify_storage()
    verify_target(spec, require_empty=mode == 'preview')
    # Still no source application freeze, catch-up or switch authorization.
    receipt.update(stage={'preview': 'full_business_preview', 'copy': 'full_business_baseline_copied',
        'compare': 'full_business_compared'}[mode], host='web-saas-prod', database='account',
        accounts_commit=spec['transfer']['accounts_commit'], image_digest=spec['transfer']['image_digest'],
        business_tables=spec['transfer']['business_tables'], writers_paused=True, independent_disk_verified=True,
        source_writers_paused=False, final_catchup_complete=False, database_cutover_approved=False)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--guard-directory', type=Path, required=True)
    parser.add_argument('--mode', choices=('preview', 'copy', 'compare'), required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    validate_spec(spec)
    if args.validate_only:
        print('Fixed full-business scope and approved source identity verified; no database action performed.')
        return
    # A line-limited JSON input avoids argv/env/file secret transport on host.
    raw = __import__('sys').stdin.readline(65537)
    native.require(len(raw.encode()) <= 65536 and raw.endswith('\n'), 'Private runtime stdin is incomplete')
    credentials = json.loads(raw)
    receipt = execute(spec, args.guard_directory, args.mode, credentials)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (native.Refused, ValueError, KeyError, TypeError, OSError):
        print(json.dumps(failure_receipt(), sort_keys=True))
        raise SystemExit(1)
