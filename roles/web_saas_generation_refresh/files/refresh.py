#!/usr/bin/env python3
"""Explicit V2 -> V3 refresh. Credentials enter through stdin only.

Generic PostgreSQL transport is reused from the existing Sandbox owner module;
the Sandbox deployment/preparation entry point is never called.
"""
import contextlib
import datetime
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
import urllib.request

from generation_pg import DockerPG, private, require, run, sha, write_new

LIVE = 'web-saas-postgresql'
WRITERS = ['doco-cd', 'web-saas-accounts', 'web-saas-billing']
CATALOG_TABLES = {'public.billing_plans', 'public.schema_migrations', 'public.accounts_controlled_migration_history'}


def validate(c, hostname):
    require(hostname == 'web-saas-prod-v3')
    require(c['source_generation'] == 'prod-v2' and c['target_generation'] == 'prod-v3')
    require(re.fullmatch(r'[1-9][0-9]{7,19}', c['run_id']))
    require(re.fullmatch(r'prod-v3-[A-Za-z0-9._-]+', c['release_tag']))
    require(c['expected_source_version'] == 2026101703 and c['target_version'] == 2026101902)
    require(c['public_dns_cutover'] is False and c['allow_refresh'] is True)
    require(re.fullmatch(r'[0-9a-f]{40}', c['gitops_sha']))
    require(re.fullmatch(r'[0-9a-f]{64}', c['migration_binary_sha256']))
    require(c['migration_binary'] == '/usr/local/libexec/accounts-reconcile/' + c['migration_binary_sha256'])
    require(len(c['passphrase']) >= 32)
    u = urllib.parse.urlsplit(c['source_dsn'])
    require(u.scheme in ('postgres', 'postgresql') and u.hostname == '127.0.0.1')
    require(u.path == '/account' and u.port and u.username == 'postgres' and u.password)
    require([s['target'] for s in c['migrations']] == [2026101901, 2026101902])
    require([s['expected'] for s in c['migrations']] == [2026101703, 2026101901])
    require([s['sha256'] for s in c['migrations']] == [
        'b9fee298512e7d888fe619917c8238120f0e227f4ea348e59397d074c41f878d',
        '53f9aaf7c56a14fd7c132c6649b79582cd2063f8903b94efb4e23cbf4895437c'])


class DB(DockerPG):
    def __init__(self, database='account', container=LIVE):
        super().__init__(container)
        require(re.fullmatch(r'account(?:_[a-z]+_[0-9]+)?|postgres', database))
        self.database = database

    def psql_command(self):
        return self.command('psql', '-d', self.database, '-XAtq', '-v', 'ON_ERROR_STOP=1')

    def sql(self, sql, snapshot=None):
        sql = "SET timezone='UTC'; SET datestyle='ISO,YMD'; SET extra_float_digits=3;\n" + sql.rstrip().rstrip(';') + ';'
        return super().sql(sql, snapshot)


class Source(DB):
    def __init__(self, c, image):
        super().__init__()
        self.image = image
        u = urllib.parse.urlsplit(c['source_dsn'])
        os.environ.update(PGHOST='127.0.0.1', PGPORT=str(u.port), PGUSER='postgres',
            PGPASSWORD=urllib.parse.unquote(u.password), PGCONNECT_TIMEOUT='15',
            PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=120000 -c idle_in_transaction_session_timeout=600000 -c timezone=UTC -c extra_float_digits=3')

    def command(self, program, *args):
        require(program in ('psql', 'pg_dump'))
        command = ['docker', 'run', '--rm', '-i', '--network', 'host', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges']
        for name in ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGCONNECT_TIMEOUT', 'PGOPTIONS'):
            command += ['--env', name]
        return command + ['--entrypoint', program, self.image, '-U', 'postgres', *args]


def fingerprints(db, snapshot=None):
    tables = json.loads(db.sql("""SELECT coalesce(json_agg(n.nspname||'.'||c.relname ORDER BY n.nspname,c.relname),'[]')
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('public','finance') AND c.relkind IN ('r','p') AND NOT c.relispartition""", snapshot))
    require({'public.users', 'public.subscription_records', 'public.billing_ledger'} <= set(tables))
    queries = []
    for table in tables:
        require(re.fullmatch(r'(public|finance)\.[a-z_][a-z0-9_]*', table))
        schema, name = table.split('.')
        queries.append("SELECT '" + table + "' AS name, count(*) AS rows, "
            "encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text COLLATE \"C\"),''),'UTF8')),'hex') AS sha256 FROM \"" + schema + '\".\"' + name + '\" t')
    result = json.loads(db.sql('SELECT json_object_agg(name,json_build_object(\'rows\',rows,\'sha256\',sha256)) FROM (' + ' UNION ALL '.join(queries) + ') f', snapshot))
    require(result['public.users']['rows'] > 0 and result['public.subscription_records']['rows'] > 0)
    return result


def state(db, expected):
    require(db.sql("SELECT version::text||':'||dirty::text FROM public.schema_migrations") == str(expected) + ':false')
    require(db.sql("SELECT current_setting('server_version_num')::int BETWEEN 170000 AND 179999") == 't')


def write_receipt(path, value):
    write_new(path, json.dumps(value, sort_keys=True).encode())


def load_receipt(path):
    private(path)
    return json.loads(path.read_text())


def encrypted_dump(db, archive, passphrase, snapshot):
    os.environ['WEB_SAAS_REFRESH_KEY'] = passphrase
    with archive.open('xb') as output:
        os.chmod(archive, 0o600)
        dump = subprocess.Popen(db.command('pg_dump', '-d', db.database, '--format=custom',
            '--no-owner', '--no-privileges', '--snapshot=' + snapshot), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        crypt = subprocess.Popen(['openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-iter', '200000',
            '-pass', 'env:WEB_SAAS_REFRESH_KEY'], stdin=dump.stdout, stdout=output, stderr=subprocess.PIPE)
        dump.stdout.close(); dump.stdout = None
        try:
            crypt.communicate(timeout=600); dump.communicate(timeout=30)
            require(crypt.returncode == 0 and dump.returncode == 0)
            output.flush(); os.fsync(output.fileno())
        finally:
            for p in (crypt, dump):
                if p.poll() is None: p.kill(); p.wait()


def restore_archive(c, archive, database):
    admin = DB('postgres')
    require(admin.sql("SELECT count(*) FROM pg_database WHERE datname='" + database + "'") == '0')
    admin.sql('CREATE DATABASE ' + database + ' TEMPLATE template0')
    db = DB(database)
    oid = admin.sql("SELECT oid FROM pg_database WHERE datname='" + database + "'")
    # PostgreSQL 17 dumps omit creation of the standard public schema.
    # Keep template0's empty schema; this is a new database, never a reused one.
    os.environ['WEB_SAAS_REFRESH_KEY'] = c['passphrase']
    crypt = subprocess.Popen(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000',
        '-pass', 'env:WEB_SAAS_REFRESH_KEY', '-in', str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    restore = subprocess.Popen(db.command('pg_restore', '--dbname=' + database, '--no-owner', '--no-privileges',
        '--single-transaction', '--exit-on-error'), stdin=crypt.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    crypt.stdout.close(); crypt.stdout = None
    try:
        _, restore_error = restore.communicate(timeout=600)
        crypt.communicate(timeout=30)
        if restore.returncode:
            raise subprocess.CalledProcessError(restore.returncode, ['pg_restore'], stderr=restore_error)
        require(crypt.returncode == 0)
    finally:
        for p in (restore, crypt):
            if p.poll() is None: p.kill(); p.wait()
    return db, oid


def source_snapshot(c, root, image):
    require(not (root / 'source-snapshot.json').exists())
    source = Source(c, image)
    archive = root / 'v2-account.dump.enc'
    os.environ['WEB_SAAS_REFRESH_KEY'] = c['passphrase']
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with source.snapshot() as snapshot:
        require(source.sql("SELECT current_setting('transaction_read_only')='on' AND current_setting('default_transaction_read_only')='on'", snapshot) == 't')
        require(source.sql("SELECT version::text||':'||dirty::text FROM public.schema_migrations", snapshot) == str(c['expected_source_version']) + ':false')
        before = fingerprints(source, snapshot)
        encrypted_dump(source, archive, c['passphrase'], snapshot)
    evidence = dict(source_generation='prod-v2', target_generation='prod-v3', snapshot_started_at=started,
        snapshot_id=snapshot, archive_sha256=sha(archive), source_schema_version=c['expected_source_version'],
        source_read_only=True, fingerprints=before, sequence_semantics='pg_dump captures non-MVCC sequence values; no source nextval calls')
    write_receipt(root / 'source-snapshot.json', evidence)
    return evidence


def restore_candidate(c, root):
    source = load_receipt(root / 'source-snapshot.json')
    archive = root / 'v2-account.dump.enc'; private(archive)
    require(sha(archive) == source['archive_sha256'])
    candidate = 'account_candidate_' + c['run_id']
    require(not (root / 'candidate-restored.json').exists())
    db, oid = restore_archive(c, archive, candidate)
    state(db, c['expected_source_version'])
    after = fingerprints(db)
    require(after == source['fingerprints'])
    evidence = dict(candidate_database=candidate, candidate_oid=oid, archive_sha256=source['archive_sha256'],
        source_fingerprints_equal=True, fingerprints=after, schema_version=c['expected_source_version'], dirty=False)
    write_receipt(root / 'candidate-restored.json', evidence)
    return evidence


def upgrade_candidate(c, root, image):
    restored = load_receipt(root / 'candidate-restored.json')
    candidate = 'account_candidate_' + c['run_id']
    require(restored['candidate_database'] == candidate)
    require(DB('postgres').sql("SELECT oid FROM pg_database WHERE datname='" + candidate + "'") == restored['candidate_oid'])
    require(not (root / 'candidate-upgraded.json').exists())
    db = DB(candidate)
    state(db, c['expected_source_version'])
    require(fingerprints(db) == restored['fingerprints'])
    binary = Path(c['migration_binary'])
    require(binary.stat().st_uid == os.geteuid() and binary.stat().st_mode & 0o777 == 0o500)
    require(not binary.is_symlink() and sha(binary) == c['migration_binary_sha256'])
    info = json.loads(run(['docker', 'inspect', LIVE]))[0]
    config = dict(value.split('=', 1) for value in info['Config']['Env'])
    password = config['POSTGRES_PASSWORD']
    os.environ['ACCOUNTS_MIGRATION_DSN'] = 'postgresql://postgres:' + urllib.parse.quote(password, safe='') + '@127.0.0.1:5432/' + candidate + '?sslmode=disable'
    steps = []
    for index, step in enumerate(c['migrations']):
        state(db, step['expected'])
        archive = root / ('before-' + str(step['target']) + '.dump.enc')
        with db.snapshot() as snapshot:
            before = fingerprints(db, snapshot)
            encrypted_dump(db, archive, c['passphrase'], snapshot)
        verify_db, verify_oid = restore_archive(c, archive, 'account_verify_' + c['run_id'] + str(index))
        state(verify_db, step['expected'])
        require(fingerprints(verify_db) == before)
        run(['docker', 'run', '--rm', '--read-only', '--network', 'container:' + LIVE,
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--mount', 'type=bind,source=' + str(binary) + ',target=/migratectl,readonly',
            '--env', 'ACCOUNTS_MIGRATION_DSN', '--entrypoint', '/migratectl', image,
            'controlled-migrate', '--expected-version', str(step['expected']), '--target-version', str(step['target']),
            '--sha256', step['sha256'], '--lock-timeout', '10s', '--statement-timeout', '5m'])
        state(db, step['target'])
        after = fingerprints(db)
        require({k:v for k,v in before.items() if k not in CATALOG_TABLES} ==
                {k:v for k,v in after.items() if k not in CATALOG_TABLES})
        steps.append(dict(**step, backup_sha256=sha(archive), isolated_restore_verified=True,
            restored_database_oid=verify_oid, protected_data_equal=True))
        write_receipt(root / ('migration-' + str(step['target']) + '.json'), steps[-1])
    result = dict(candidate_database=candidate, candidate_oid=restored['candidate_oid'],
        schema_version=c['target_version'], dirty=False, migrations=steps,
        source_business_fingerprints_equal=True, fingerprints=fingerprints(db))
    write_receipt(root / 'candidate-upgraded.json', result)
    return result


def ident(name):
    require(re.fullmatch(r'[a-z_][a-z0-9_]*', name))
    return '"' + name + '"'


def candidate_permissions(db):
    """Copy only the existing nonprivileged app role's object-level permissions.

    Passwords, role attributes, global/default privileges and source roles are
    never restored. The retained V3 database is the permission reference.
    """
    old = DB()
    require(old.sql("SELECT count(*) FROM pg_roles WHERE rolname='recovery_app' AND NOT rolsuper AND NOT rolbypassrls") == '1')
    tables = json.loads(old.sql("""SELECT json_agg(json_build_object('schema',schemaname,'name',tablename,'owner',tableowner))
        FROM pg_tables WHERE schemaname IN ('public','finance')"""))
    commands = []
    for table in tables:
        require(table['owner'] in ('postgres', 'recovery_app'))
        relation = ident(table['schema']) + '.' + ident(table['name'])
        require(db.sql("SELECT to_regclass('" + relation + "') IS NOT NULL") == 't')
        if table['owner'] == 'recovery_app':
            commands.append('ALTER TABLE ' + relation + ' OWNER TO recovery_app')
    grants = json.loads(old.sql("""SELECT coalesce(json_agg(json_build_object('schema',table_schema,'name',table_name,'privilege',privilege_type)),'[]')
        FROM information_schema.role_table_grants WHERE grantee='recovery_app' AND table_schema IN ('public','finance')"""))
    for grant in grants:
        require(grant['privilege'] in ('SELECT','INSERT','UPDATE','DELETE','REFERENCES','TRIGGER','TRUNCATE'))
        commands.append('GRANT ' + grant['privilege'] + ' ON TABLE ' + ident(grant['schema']) + '.' + ident(grant['name']) + ' TO recovery_app')
    for schema in ('public', 'finance'):
        if old.sql("SELECT to_regnamespace('" + schema + "') IS NOT NULL") == 't':
            for privilege in ('USAGE', 'CREATE'):
                if old.sql("SELECT has_schema_privilege('recovery_app','" + schema + "','" + privilege + "')") == 't':
                    commands.append('GRANT ' + privilege + ' ON SCHEMA ' + ident(schema) + ' TO recovery_app')
    sequences = json.loads(old.sql("""SELECT coalesce(json_agg(json_build_object('schema',schemaname,'name',sequencename)),'[]')
        FROM pg_sequences WHERE schemaname IN ('public','finance')"""))
    for sequence in sequences:
        relation = ident(sequence['schema']) + '.' + ident(sequence['name'])
        for privilege in ('USAGE', 'SELECT', 'UPDATE'):
            if old.sql("SELECT has_sequence_privilege('recovery_app','" + relation + "','" + privilege + "')") == 't':
                commands.append('GRANT ' + privilege + ' ON SEQUENCE ' + relation + ' TO recovery_app')
    db.sql('BEGIN; SET LOCAL lock_timeout=\'10s\';\n' + ';\n'.join(commands) + ';\nCOMMIT;')
    require(db.sql("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='recovery_app' AND table_schema IN ('public','finance')") ==
            old.sql("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='recovery_app' AND table_schema IN ('public','finance')"))
    return dict(application_role='recovery_app', role_attributes_unchanged=True, object_grants_matched=True)


def sandbox_catalog(c, db):
    """Keep only reviewed V3 test price bindings, never V2 live bindings."""
    key = c['stripe_sandbox_key']
    require(key.startswith('sk_test_'))
    plans = ['PLUS', 'PRO-MONTHLY-V2', 'PRO-YEARLY-V2', 'PAYG-TOPUP-50',
        'PAYG-TOPUP-100', 'PAYG-TOPUP-200', 'PAYG-TOPUP-USD-20',
        'PAYG-TOPUP-USD-50', 'PAYG-TOPUP-USD-100']
    selection = ','.join("'" + plan + "'" for plan in plans)
    fields = 'plan_id,stripe_price_id,price_amount,price_currency,price_unit,included_quota_bytes'
    rows = json.loads(DB().sql('SELECT json_agg(t) FROM (SELECT ' + fields + ' FROM billing_plans WHERE plan_id IN (' + selection + ')) t'))
    require(len(rows) == len(plans))
    commands = ['UPDATE billing_plans SET stripe_price_id=NULL WHERE stripe_price_id IS NOT NULL']
    for row in rows:
        price_id = row.pop('stripe_price_id')
        require(re.fullmatch(r'price_[A-Za-z0-9]+', price_id))
        expected = json.loads(db.sql("SELECT row_to_json(t) FROM (SELECT " + fields.replace('stripe_price_id,', '') + " FROM billing_plans WHERE plan_id='" + row['plan_id'] + "') t"))
        require(row == expected)
        request = urllib.request.Request('https://api.stripe.com/v1/prices/' + price_id,
            headers={'Authorization': 'Bearer ' + key})
        with urllib.request.urlopen(request, timeout=30) as response:
            price = json.load(response)
        require(price['livemode'] is False and price['active'] is True)
        require(price['unit_amount'] == row['price_amount'] and price['currency'].upper() == row['price_currency'].upper())
        recurring = price.get('recurring')
        require((row['price_unit'] == 'once' and recurring is None) or
                (recurring is not None and recurring['interval'] == row['price_unit'] and recurring['interval_count'] == 1))
        commands.append("UPDATE billing_plans SET stripe_price_id='" + price_id + "' WHERE plan_id='" + row['plan_id'] + "'")
    db.sql("BEGIN; SET LOCAL lock_timeout='10s';" + ';'.join(commands) + ';COMMIT;')
    return dict(reviewed_sandbox_prices=len(rows), all_prices_test_mode=True, source_live_bindings_cleared=True)


def activate(c, root):
    upgraded = load_receipt(root / 'candidate-upgraded.json')
    candidate = 'account_candidate_' + c['run_id']
    previous = 'account_before_' + c['run_id']
    require(upgraded['candidate_database'] == candidate)
    require(not (root / 'activated.json').exists())
    admin = DB('postgres')
    require(admin.sql("SELECT oid FROM pg_database WHERE datname='" + candidate + "'") == upgraded['candidate_oid'])
    require(admin.sql("SELECT count(*) FROM pg_database WHERE datname='" + previous + "'") == '0')
    db = DB(candidate)
    state(db, c['target_version'])
    require(fingerprints(db) == upgraded['fingerprints'])
    catalog = sandbox_catalog(c, db)
    permissions = candidate_permissions(db)
    active_fingerprints = fingerprints(db)
    require({k:v for k,v in active_fingerprints.items() if k not in CATALOG_TABLES} ==
            {k:v for k,v in upgraded['fingerprints'].items() if k not in CATALOG_TABLES})
    old_oid = admin.sql("SELECT oid FROM pg_database WHERE datname='account'")
    require(old_oid.isdigit())
    require(admin.sql("SELECT count(*) FROM pg_stat_activity WHERE datname IN ('account','" + candidate + "')") == '0')
    # Both renames are one transaction. On any failure both original names stay.
    admin.sql("BEGIN; SET LOCAL lock_timeout='10s'; SET LOCAL statement_timeout='30s'; "
        'ALTER DATABASE account RENAME TO ' + previous + '; '
        'ALTER DATABASE ' + candidate + ' RENAME TO account; COMMIT;')
    require(admin.sql("SELECT oid FROM pg_database WHERE datname='account'") == upgraded['candidate_oid'])
    require(admin.sql("SELECT oid FROM pg_database WHERE datname='" + previous + "'") == old_oid)
    state(DB(), c['target_version'])
    require(fingerprints(DB()) == active_fingerprints)
    result = dict(previous_database=previous, previous_oid=old_oid,
        active_database='account', active_oid=upgraded['candidate_oid'], schema_version=c['target_version'],
        dirty=False, data_matches_upgraded_candidate=True, permissions=permissions,
        release_tag=c['release_tag'], gitops_sha=c['gitops_sha'],
        migrations=upgraded['migrations'], fingerprints=active_fingerprints, catalog=catalog, public_dns_changed=False)
    write_receipt(root / 'activated.json', result)
    return result


def verify_runtime(c, root):
    activated = load_receipt(root / 'activated.json')
    require(DB('postgres').sql("SELECT oid FROM pg_database WHERE datname='account'") == activated['active_oid'])
    state(DB(), c['target_version'])
    require(fingerprints(DB()) == activated['fingerprints'])
    require(DB('postgres').sql("SELECT oid FROM pg_database WHERE datname='" + activated['previous_database'] + "'") == activated['previous_oid'])
    components = {}
    for component, name in [('accounts','web-saas-accounts'), ('billing','web-saas-billing'), ('console','web-saas-console')]:
        info = json.loads(run(['docker', 'inspect', name]))[0]
        require(info['State']['Running'] is True)
        require(info['Config']['Image'].endswith('@' + c['components'][component]['image_digest']))
        components[component] = dict(image=info['Config']['Image'], source_sha=c['components'][component]['source_sha'],
            restart_count=info['RestartCount'], rollback_digest=c['components'][component]['rollback_digest'])
    checks = {}
    for component in ('accounts', 'billing'):
        url = 'https://' + component + '-selfhost-prod-v3.svc.plus/readyz'
        with urllib.request.urlopen(url, timeout=30) as response:
            require(response.status == 200)
            checks[component] = response.status
    controller = json.loads(run(['docker', 'inspect', 'doco-cd']))[0]
    require(controller['State']['Running'] is True and controller['State']['Health']['Status'] == 'healthy')
    result = dict(status='passed', release_tag=c['release_tag'], gitops_sha=c['gitops_sha'],
        source_generation=c['source_generation'], target_generation=c['target_generation'],
        schema_version=c['target_version'], dirty=False, components=components, readiness=checks,
        restored_business_data_unchanged=True, previous_database_retained=True,
        public_dns_changed=False, real_payment_tested=False, password_login_tested=False,
        snapshot_boundary=load_receipt(root / 'source-snapshot.json')['snapshot_started_at'])
    write_receipt(root / 'runtime-verified.json', result)
    return result


def verify_reconciliation(c, root):
    runtime = load_receipt(root / 'runtime-verified.json')
    require(runtime['status'] == 'passed' and runtime['gitops_sha'] == c['gitops_sha'])
    info = json.loads(run(['docker', 'inspect', 'doco-cd']))[0]
    env = dict(value.split('=', 1) for value in info['Config']['Env'])
    polls = json.loads(env['POLL_CONFIG'])
    require(len(polls) == 1)
    require(polls[0]['reference'] == 'refs/tags/' + c['release_tag'])
    require(polls[0]['target'] == 'prod-v3-stripe-sandbox')
    require(polls[0]['url'] == 'https://github.com/ai-workspace-infra/gitops.git')
    logs = subprocess.run(['docker','logs','--since',info['State']['StartedAt'],'doco-cd'],
        capture_output=True, check=True)
    text = (logs.stdout + logs.stderr).decode()
    # Never publish raw logs (repository URLs can contain credentials).
    result = dict(status='passed', gitops_sha=c['gitops_sha'],
        release_tag=c['release_tag'], target=polls[0]['target'],
        configured_reference_matches=True, expected_commit_observed=c['gitops_sha'] in text,
        expected_tag_observed=c['release_tag'] in text,
        controller_healthy=info['State']['Health']['Status'] == 'healthy')
    require(result['expected_commit_observed'] and result['expected_tag_observed'] and result['controller_healthy'])
    write_receipt(root / 'reconciliation-verified.json', result)
    return result


def paused():
    for name in WRITERS:
        require(run(['docker', 'inspect', '--format', '{{.State.Running}}', name]).decode().strip() == 'false')


def main():
    os.umask(0o077)
    c = json.load(sys.stdin)
    validate(c, socket.gethostname().split('.')[0])
    require(os.geteuid() == 0)
    require(run(['findmnt', '-nro', 'TARGET', '--mountpoint', '/data']).decode().strip() == '/data')
    live = json.loads(run(['docker', 'inspect', LIVE]))[0]
    require(live['Name'] == '/' + LIVE and live['State']['Running'])
    require(any(m['Type'] == 'bind' and m['Source'] == '/data/postgresql' for m in live['Mounts']))
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', live['Image']))
    root = Path('/data/backups/web-saas/prod-v3') / c['release_tag'] / c['run_id']
    private(root, directory=True)
    backup, restored = c['backup'], c['restore']
    require(backup['status'] == restored['status'] == 'passed')
    require(backup['archive_sha256'] == restored['archive_sha256'])
    require(restored['isolated_restore_verified'] is True and restored['restored_data_matches'] is True)
    require(backup['archive_path'] == str(root / 'account.dump.enc'))
    require(sha(Path(backup['archive_path'])) == backup['archive_sha256'])
    require(backup['database_system_identifier'] == DB('postgres').sql('SELECT system_identifier FROM pg_control_system()'))
    with (root / 'refresh.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        phase = sys.argv[1]
        require(phase in ('snapshot', 'restore_candidate', 'upgrade_candidate', 'activate', 'verify_runtime', 'verify_reconciliation'))
        if phase not in ('verify_runtime', 'verify_reconciliation'):
            paused()
        if phase == 'snapshot':
            result = source_snapshot(c, root, live['Image'])
        elif phase == 'restore_candidate':
            result = restore_candidate(c, root)
        elif phase == 'upgrade_candidate':
            result = upgrade_candidate(c, root, live['Image'])
        elif phase == 'activate':
            result = activate(c, root)
        elif phase == 'verify_runtime':
            result = verify_runtime(c, root)
        else:
            result = verify_reconciliation(c, root)
        print(json.dumps(dict(phase=phase, result='passed', evidence=result), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'result':'stopped','reason':type(exc).__name__, 'old_database_preserved':True}), file=sys.stderr)
        sys.exit(2)
