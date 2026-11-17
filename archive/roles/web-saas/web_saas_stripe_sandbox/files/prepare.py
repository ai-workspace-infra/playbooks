#!/usr/bin/env python3
"""V3-only host preparation. No source connection or application deployment.

Secrets arrive on stdin; errors deliberately exclude exception text, SQL,
subprocess output, environment and configuration contents.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit, parse_qs, unquote

CONFIG = Path('/etc/xcontrol/web-saas-recovery')
BACKUPS = Path('/data/backups/web-saas/prod-v3')
RESTORED = Path('/data/recovery/prod-v3/restored.json')
LIVE = 'web-saas-postgresql'
REQUIRED = {'public.users', 'public.subscription_records', 'public.billing_ledger'}
CADDY = 'web-saas-caddy'
TLS_DIR = Path('/etc/xcontrol/tls/svc.plus')


def require(condition):
    if not condition:
        raise ValueError('Sandbox safety gate failed')


def contract(c, hostname):
    require(c['generation'] == 'prod-v3' and hostname == 'web-saas-prod-v3')
    require(re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', c['domain']))
    require(re.fullmatch(r'prod-v3-[A-Za-z0-9][A-Za-z0-9._-]*', c['release_tag']))
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', c['run_id']))
    require(re.fullmatch(r'[1-9][0-9]*', c['schema_version']))
    require(c['users'] is None or (type(c['users']) is int and c['users'] > 0))
    require(all(re.fullmatch(r'[0-9a-f]{64}', c[key]) for key in ('iac_inventory_sha256', 'iac_receipt_sha256')))
    declaration = c['declaration']
    target, stripe, database = (declaration[key] for key in ('target', 'stripe', 'database'))
    require(declaration['apiVersion'] == 'web-saas/v1' and declaration['kind'] == 'StripeSandboxQualification')
    require(target['generation'] == 'prod-v3' and target['resource_name'] == 'web-saas-prod-v3')
    public_domains = target['public_domains']
    require(public_domains == {name: name + '.' + c['domain'] for name in ('console', 'accounts', 'billing')})
    for prefix in ('console', 'accounts', 'billing', 'postgresql'):
        require(target[prefix + '_domain'] == prefix + '-selfhost-prod-v3.' + c['domain'])
    require(target['doco_target'] == 'prod-v3-stripe-sandbox')
    require(database['name'] == 'account' and str(database['expected_schema_version']) == c['schema_version'])
    require(database['background_writers'] is False and database['preserve_existing_accounts'] is True)
    require(stripe['mode'] == 'test' and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\.[a-z]+', stripe['api_version']))
    require(stripe['vault_path'] == 'kv/prod/billing-service')
    require(stripe['secret_key'] == 'SANDBOX_STRIPE_SECRET_KEY')
    require(stripe['webhook_secret_key'] == 'SANDBOX_STRIPE_WEBHOOK_SECRET')
    require(stripe['webhook_path'] == '/api/billing/stripe/webhook')
    require(stripe['webhook_environment'] == target['generation'] == 'prod-v3')
    require(stripe['webhook_registry_ref'] == 'compose/web-saas/billing/stripe-webhooks.yaml')
    require(stripe['webhook_url'] == 'https://' + target['accounts_domain'] + stripe['webhook_path'])
    isolation = declaration['isolation']
    require(isolation['source_access'] == 'none')
    require(all(isolation[key] is False for key in ('public_dns_cutover', 'postgres_public_port', 'smtp_enabled')))


def declaration_digest(c):
    return hashlib.sha256(json.dumps(c['declaration'], sort_keys=True).encode()).hexdigest()


def run(args, *, data=None, source=None, output=subprocess.PIPE):
    return subprocess.run(args, input=data, stdin=source, stdout=output,
                          stderr=subprocess.PIPE, check=True).stdout


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def private(path, directory=False):
    require(path.resolve() == path and not path.is_symlink())
    info = path.stat()
    # main requires euid=0; using the effective identity also permits isolated tests.
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))


def mkdir(path):
    # Check every ancestor; refuse symlink traversal, never chmod a mount root.
    require(path.resolve() == path)
    path.mkdir(mode=0o700, exist_ok=True)
    private(path, directory=True)


def write_new(path, data):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def replace_private(path, data):
    require(path.resolve() == path and not path.is_symlink())
    if path.exists():
        private(path)
    staging = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        write_new(staging, data)
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


class DockerPG:
    def __init__(self, container=LIVE):
        self.container = container

    def command(self, program, *args):
        return ['docker', 'exec', '-i', self.container, program, '-U', 'postgres', *args]

    def psql_command(self):
        return self.command('psql', '-d', 'account', '-XAtq', '-v', 'ON_ERROR_STOP=1')

    def sql(self, sql, snapshot=None):
        if snapshot:
            require(re.fullmatch(r'[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', snapshot))
            sql = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '"
                   + snapshot + "';\n" + sql + '\nCOMMIT;')
        return run(self.psql_command(), data=sql.encode()).decode().strip()

    @contextlib.contextmanager
    def snapshot(self):
        process = subprocess.Popen(self.psql_command(), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n')
            process.stdin.flush()
            snapshot = process.stdout.readline().decode().strip()
            require(re.fullmatch(r'[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', snapshot))
            yield snapshot
            process.stdin.write(b'ROLLBACK;\n')
            process.stdin.close()
            require(process.wait(timeout=30) == 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()

    def dump(self, path, snapshot):
        with path.open('xb') as stream:
            os.chmod(path, 0o600)
            run(self.command('pg_dump', '-d', 'account', '--format=custom', '--snapshot=' + snapshot), output=stream)
            stream.flush()
            os.fsync(stream.fileno())
        with path.open('rb') as stream:
            require(stream.read(5) == b'PGDMP')

    def restore(self, path):
        # Only this newly created isolated database may have its empty public removed.
        require(self.container != LIVE)
        run(self.command('createdb', '--template=template0', 'account'))
        with path.open('rb') as stream:
            toc = run(['docker', 'exec', '-i', self.container, 'pg_restore', '--list'], source=stream).decode()
        if re.search(r'^\d+; \d+ \d+ SCHEMA - public(?: |$)', toc, re.MULTILINE):
            self.sql('DROP SCHEMA public;')
        with path.open('rb') as stream:
            run(self.command('pg_restore', '--dbname=account', '--no-owner', '--no-privileges',
                             '--single-transaction', '--exit-on-error'), source=stream)


def observe(db, c, snapshot=None):
    migrations = db.sql("SELECT version::text || ':' || dirty::text FROM public.schema_migrations;", snapshot)
    require(migrations == c['schema_version'] + ':false')
    tables = json.loads(db.sql("""SELECT COALESCE(json_agg(n.nspname || '.' || c.relname ORDER BY n.nspname,c.relname),'[]')
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('public','finance') AND c.relkind IN ('r','p') AND NOT c.relispartition;""", snapshot))
    require(REQUIRED <= set(tables))
    counts = {}
    for table in tables:
        require(re.fullmatch(r'(public|finance)\.[a-z_][a-z0-9_]*', table))
        schema, name = table.split('.')
        counts[table] = int(db.sql('SELECT count(*) FROM "' + schema + '"."' + name + '";', snapshot))
    if c['users'] is not None:
        require(counts['public.users'] == c['users'])
    # Finance is distinct from the historical public billing ledger. Preserve
    # and count that ledger; do not infer it is empty from "finance tables=0".
    finance = {name: count for name, count in counts.items()
               if name.startswith(('finance.', 'public.finance_'))}
    return {'schema_version': c['schema_version'], 'dirty': False, 'counts': counts,
            'finance_counts': finance,
            'server_version_num': db.sql("SELECT current_setting('server_version_num');", snapshot)}


def inspect_live():
    info = json.loads(run(['docker', 'inspect', LIVE]))[0]
    mounts = [m for m in info['Mounts'] if m['Destination'] == '/var/lib/postgresql/data']
    require(len(mounts) == 1 and mounts[0]['Type'] == 'bind' and mounts[0]['Source'] == '/data/postgresql')
    require(info['State']['Running'] and re.fullmatch(r'sha256:[0-9a-f]{64}', info['Image']))
    # The reserved PostgreSQL alias is never published, even on loopback.
    require(not info.get('HostConfig', {}).get('PortBindings', {}).get('5432/tcp'))
    require(info.get('HostConfig', {}).get('NetworkMode') != 'host')
    require(not info.get('NetworkSettings', {}).get('Ports', {}).get('5432/tcp'))
    require(run(['findmnt', '-nro', 'TARGET', '--mountpoint', '/data']).decode().strip() == '/data')
    private(RESTORED)
    previous = json.loads(RESTORED.read_text())
    require(previous['generation'] == 'prod-v3' and previous['dirty'] is False)
    db = DockerPG()
    require(previous['database_oid'] == db.sql("SELECT oid FROM pg_database WHERE datname='account';"))
    require(previous['system_identifier'] == db.sql('SELECT system_identifier FROM pg_control_system();'))
    require(db.sql("SELECT NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND rolcanlogin FROM pg_roles WHERE rolname='account_user';") == 't')
    require(db.sql("""SELECT bool_and(has_table_privilege('account_user',c.oid,'INSERT')
AND has_table_privilege('account_user',c.oid,'SELECT')) FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','finance') AND c.relkind IN ('r','p')
AND c.relname <> 'schema_migrations' AND c.relname !~ '^(migratectl_|migration_)';""") == 't')
    return info['Image'], {'database_oid': previous['database_oid'], 'system_identifier': previous['system_identifier']}


@contextlib.contextmanager
def isolated_pg(image):
    # Random names are created here, never supplied by a caller. No host ports,
    # source data volumes, production network, or production credentials.
    name = 'v3-stripe-restore-' + uuid.uuid4().hex
    network_created = container_created = False
    try:
        run(['docker', 'network', 'create', '--internal', name])
        network_created = True
        run(['docker', 'create', '--name', name, '--network', name, '--label',
             'web-saas.stripe-sandbox.restore=true', '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
             '--mount', 'type=volume,destination=/var/lib/postgresql/data', image])
        container_created = True
        info = json.loads(run(['docker', 'inspect', name]))[0]
        network = json.loads(run(['docker', 'network', 'inspect', name]))[0]
        require(network['Internal'] and info['HostConfig']['NetworkMode'] == name)
        require(not info['HostConfig'].get('PortBindings'))
        require(all(m.get('Type') != 'bind' for m in info['Mounts']))
        require(info['Image'] == image)
        run(['docker', 'start', name])
        for _ in range(60):
            try:
                # The init server accepts Unix sockets before it shuts down.
                # Only the final server accepts loopback TCP connections.
                run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'])
                break
            except subprocess.CalledProcessError:
                time.sleep(1)
        else:
            raise ValueError('isolated PostgreSQL did not become ready')
        yield DockerPG(name)
    finally:
        # Only this run's randomly named disposable resources are removed.
        # Checkpoint archive and receipts are always retained, including failure.
        try:
            if container_created:
                run(['docker', 'rm', '--force', '--volumes', name])
        finally:
            if network_created:
                run(['docker', 'network', 'rm', name])


def checkpoint(db, isolated, root, c):
    dump = root / 'account.dump'
    with db.snapshot() as snapshot:
        before = observe(db, c, snapshot)
        db.dump(dump, snapshot)
    private(dump)
    with isolated() as restored:
        restored.restore(dump)
        after = observe(restored, c)
        require(before == after)
    evidence = {**before, 'archive_sha256': sha(dump), 'restore_verified': True,
                'expected_users': before['counts']['public.users'],
                'webhook_url': c['declaration']['stripe']['webhook_url'],
                'iac_inventory_sha256': c['iac_inventory_sha256'],
                'iac_receipt_sha256': c['iac_receipt_sha256'],
                'declaration_sha256': declaration_digest(c)}
    write_new(root / 'checkpoint.json', json.dumps(evidence, sort_keys=True).encode())
    return evidence


def env_values(data):
    values = {}
    for line in data.decode().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            require(key not in values)
            values[key] = value
    return values


def sandbox_env(original, c):
    require(re.fullmatch(r'sk_test_[A-Za-z0-9]+', c['stripe_key']))
    require(re.fullmatch(r'whsec_[A-Za-z0-9]+', c['webhook_secret']))
    values = env_values(original)
    require(values['DATABASE_BACKGROUND_WRITERS'] == 'false')
    for key in ('POSTGRES_PASSWORD', 'AUTH_TOKEN_PUBLIC_TOKEN',
                'AUTH_TOKEN_REFRESH_SECRET', 'AUTH_TOKEN_ACCESS_SECRET', 'INTERNAL_SERVICE_TOKEN'):
        require(bool(values[key]))
    # Database credentials are supplied only by the shared canonical env file.
    require(not {'DATABASE_URL', 'DATABASE_RUNTIME_ROLE', 'DATABASE_IDENTITY_SHA256'} & values.keys())
    replacements = {'STRIPE_SECRET_KEY': c['stripe_key'], 'STRIPE_WEBHOOK_SECRET': c['webhook_secret']}
    frontend = 'https://' + c['declaration']['target']['console_domain']
    # Accounts deployments have used both spellings; defining both is harmless.
    replacements.update(STRIPE_FRONTEND_URL=frontend, STRIPE_FRONTEND_BASE_URL=frontend)
    replacements.update(STRIPE_API_VERSION=c['declaration']['stripe']['api_version'],
                        STRIPE_MODE=c['declaration']['stripe']['mode'],
                        STRIPE_WEBHOOK_URL=c['declaration']['stripe']['webhook_url'])
    replaced_keys = set(replacements)
    lines = original.decode().splitlines(keepends=True)
    result = ''.join((line.split('=', 1)[0] + '=' + replacements.pop(line.split('=', 1)[0]) + '\n')
                     if line.split('=', 1)[0] in replacements else line for line in lines)
    if result and not result.endswith('\n'):
        result += '\n'
    result += ''.join(key + '=' + value + '\n' for key, value in replacements.items())
    output = result.encode()
    current = env_values(output)
    require(all(current[key] == value for key, value in values.items()
                if key not in replaced_keys))
    return output


def validate_business_database_env(data):
    """Consume the canonical owner's URL without generating or changing it."""
    values = env_values(data)
    require(values.get('DATABASE_RUNTIME_ROLE') == 'primary')
    require('DATABASE_BACKGROUND_WRITERS' not in values)
    url = values.get('DATABASE_URL', '')
    if len(url) >= 2 and url[0] == url[-1] and url[0] in ('"', "'"):
        url = url[1:-1]
    require(not any(c in url for c in '\r\n\t '))
    parsed = urlsplit(url)
    require(parsed.scheme in ('postgres', 'postgresql') and parsed.hostname == 'postgres'
            and parsed.port == 5432 and parsed.path == '/account'
            and parsed.username == 'account_user' and bool(parsed.password)
            and bool(unquote(parsed.password)) and not parsed.fragment
            and re.fullmatch(r'(?:[A-Za-z0-9_.~-]|%[0-9A-Fa-f]{2})+', parsed.password)
            and parse_qs(parsed.query, keep_blank_values=True) == {'sslmode': ['disable']})


def account_config(original, c):
    text = original.decode()
    for prefix in ('console', 'accounts'):
        url = 'https://' + c['declaration']['target'][prefix + '_domain']
        require(url in text)
        text = text.replace(url + ':8443', url)
    require(':8443' not in text and 'prod-v2' not in text)
    return text.encode()


def caddy_config(c):
    target, stripe = c['declaration']['target'], c['declaration']['stripe']
    config = ('''{
    admin off
    http_port 80
    https_port 443
}
# Reserved DNS only: POSTGRES_DOMAIN (no TCP listener).
CONSOLE_DOMAIN {
    reverse_proxy console:3000
}
ACCOUNTS_DOMAIN {
    handle WEBHOOK_PATH {
        reverse_proxy accounts:8080
    }
    handle {
        reverse_proxy accounts:8080
    }
}
BILLING_DOMAIN {
    @probe {
        method GET HEAD
        path /readyz /healthz /api/ping
    }
    handle @probe {
        reverse_proxy billing:8081
    }
    handle {
        respond 403
    }
}
''').replace('POSTGRES_DOMAIN', target['postgresql_domain']).replace('CONSOLE_DOMAIN', target['console_domain']).replace('ACCOUNTS_DOMAIN', target['accounts_domain']).replace('BILLING_DOMAIN', target['billing_domain']).replace('WEBHOOK_PATH', stripe['webhook_path'])
    cert = '/etc/xcontrol/tls/svc.plus/current'
    config += f'''# BEGIN managed shared PROD public SNI
{target['public_domains']['console']} {{
    tls {cert}/fullchain.pem {cert}/key.pem
    reverse_proxy console:3000
}}
{target['public_domains']['accounts']} {{
    tls {cert}/fullchain.pem {cert}/key.pem
    handle {stripe['webhook_path']} {{
        reverse_proxy accounts:8080
    }}
    handle {{
        reverse_proxy accounts:8080 {{
            header_up X-Forwarded-Host svc.plus
        }}
    }}
}}
{target['public_domains']['billing']} {{
    tls {cert}/fullchain.pem {cert}/key.pem
    @probe {{
        method GET HEAD
        path /readyz /healthz /api/ping
    }}
    handle @probe {{
        reverse_proxy billing:8081
    }}
    handle {{
        respond 403
    }}
}}
# END managed shared PROD public SNI
'''
    return config.encode()


def validate_caddy_config(config):
    current = TLS_DIR / 'current'
    require(TLS_DIR.is_dir() and not TLS_DIR.is_symlink())
    require(current.is_symlink() and current.resolve().parent == (TLS_DIR / 'versions').resolve())
    for name in ('fullchain.pem', 'key.pem'):
        path = current / name
        require(path.is_file() and not path.is_symlink())
    info = json.loads(run(['docker', 'inspect', CADDY]))[0]
    require(info['State']['Running'] and re.fullmatch(r'sha256:[0-9a-f]{64}', info['Image']))
    run(['docker', 'run', '--rm', '--network', 'none', '--interactive',
         '--mount', 'type=bind,src=' + str(TLS_DIR) + ',dst=/etc/xcontrol/tls/svc.plus,readonly',
         '--entrypoint', 'caddy', info['Image'], 'adapt', '--config', '/dev/stdin',
         '--adapter', 'caddyfile', '--validate'], data=config)


def verify_prepared(c, root, identity):
    private(root, directory=True)
    private(root / 'prepared.json')
    receipt = json.loads((root / 'prepared.json').read_text())
    require(receipt['generation'] == c['generation'] and receipt['release_tag'] == c['release_tag']
            and receipt['run_id'] == c['run_id'] and receipt['domain'] == c['domain']
            and receipt['identity'] == identity and receipt['restore_verified'] is True
            and receipt['schema_version'] == c['schema_version'] and receipt['dirty'] is False
            and receipt['webhook_url'] == c['declaration']['stripe']['webhook_url']
            and receipt['declaration_sha256'] == declaration_digest(c)
            and receipt['iac_inventory_sha256'] == c['iac_inventory_sha256']
            and receipt['iac_receipt_sha256'] == c['iac_receipt_sha256'])
    require(set(receipt['config_sha256']) == {'secrets.env', 'business-database.env', 'stripe-sandbox.env', 'account.yaml', 'Caddyfile'})
    for name, expected in receipt['config_sha256'].items():
        require(name in ('secrets.env', 'business-database.env', 'stripe-sandbox.env', 'account.yaml', 'Caddyfile'))
        private(CONFIG / name)
        require(sha(CONFIG / name) == expected)
    private(root / 'account.dump')
    require(sha(root / 'account.dump') == receipt['archive_sha256'])
    require(DockerPG().sql("SELECT version::text || ':' || dirty::text FROM public.schema_migrations;") == c['schema_version'] + ':false')
    require(DockerPG().sql("""SELECT setconfig @> ARRAY['default_transaction_read_only=off']
FROM pg_db_role_setting WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname='account')
AND setrole=(SELECT oid FROM pg_roles WHERE rolname='account_user');""") == 't')
    return receipt


def enable_account_writes(db):
    # Existing DML grants are deliberately not broadened.
    db.sql('ALTER ROLE account_user SET default_transaction_read_only=off; '
           'ALTER ROLE account_user IN DATABASE account SET default_transaction_read_only=off;')


def prepare(c, root, image, identity):
    business = CONFIG / 'business-database.env'
    private(business)
    validate_business_database_env(business.read_bytes())
    business_digest = sha(business)
    original = (CONFIG / 'secrets.env').read_bytes()
    original_digest = sha(CONFIG / 'secrets.env')
    # Validate candidates before allocating any disposable restore resources.
    candidates = {'stripe-sandbox.env': sandbox_env(original, c),
                  'account.yaml': account_config((CONFIG / 'account.yaml').read_bytes(), c),
                  'Caddyfile': caddy_config(c)}
    # Fail before allocating a DB checkpoint unless the already-restored
    # wildcard certificate can validate the candidate public SNI sites.
    validate_caddy_config(candidates['Caddyfile'])
    # Run IDs are single-use, including failed runs; never overwrite a checkpoint.
    root.mkdir(mode=0o700)
    private(root, directory=True)
    evidence = checkpoint(DockerPG(), lambda: isolated_pg(image), root, c)
    require(sha(business) == business_digest)
    for name in ('secrets.env', 'business-database.env', 'account.yaml', 'Caddyfile'):
        write_new(root / (name + '.before'), (CONFIG / name).read_bytes())
    require(sha(CONFIG / 'secrets.env') == original_digest)
    require(sha(business) == business_digest)
    for name, data in candidates.items():
        replace_private(CONFIG / name, data)
    # Role metadata only; no grant/password/auth change and no service restart.
    enable_account_writes(DockerPG())
    require(sha(CONFIG / 'secrets.env') == original_digest)
    receipt = {**evidence, 'generation': 'prod-v3', 'release_tag': c['release_tag'],
               'run_id': c['run_id'], 'domain': c['domain'], 'identity': identity,
               'background_writers': False, 'doco_target': c['declaration']['target']['doco_target'],
               'deployment_performed': False,
               'config_sha256': {name: sha(CONFIG / name) for name in
                                 ('secrets.env', 'business-database.env', 'stripe-sandbox.env', 'account.yaml', 'Caddyfile')}}
    write_new(root / 'prepared.json', json.dumps(receipt, sort_keys=True).encode())
    verify_prepared(c, root, identity)
    return receipt


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in ('prepare', 'verify'))
    os.umask(0o077)
    c = json.load(sys.stdin)
    contract(c, socket.gethostname().split('.')[0])
    require(os.geteuid() == 0)
    private(CONFIG, directory=True)
    for name in ('secrets.env', 'business-database.env', 'account.yaml', 'Caddyfile'):
        private(CONFIG / name)
    validate_business_database_env((CONFIG / 'business-database.env').read_bytes())
    image, identity = inspect_live()
    if sys.argv[1] == 'prepare':
        for path in (BACKUPS.parent, BACKUPS, BACKUPS / c['release_tag']):
            mkdir(path)
    root = BACKUPS / c['release_tag'] / c['run_id']
    lockpath = CONFIG / 'stripe-sandbox.lock'
    require(not lockpath.is_symlink())
    with lockpath.open('a' if sys.argv[1] == 'prepare' else 'r') as lock:
        if sys.argv[1] == 'prepare':
            os.chmod(lockpath, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sys.argv[1] == 'prepare':
            receipt = prepare(c, root, image, identity)
        else:
            receipt = verify_prepared(c, root, identity)
    # No configuration hashes, identity IDs or arbitrary DB strings on stdout.
    print(json.dumps({'result': 'prepared' if sys.argv[1] == 'prepare' else 'verified',
                      'generation': 'prod-v3', 'checkpoint': str(root),
                      'schema_version': receipt['schema_version'], 'dirty': False,
                      'counts': receipt['counts'], 'restore_verified': True,
                      'finance_counts': receipt['finance_counts'],
                      'expected_users': receipt['expected_users'],
                      'webhook_url': receipt['webhook_url'],
                      'original_secrets_unchanged': True, 'deployment_performed': False}))


def cli():
    try:
        main()
    except Exception:
        print('{"result":"stopped","detail":"Sandbox safety gate failed; private checkpoint retained"}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    cli()
