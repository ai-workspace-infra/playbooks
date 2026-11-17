# Credentials are derived at runtime from an existing Vault-injected seed.
# No credentials are printed, committed, or supplied on process command lines.
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from urllib.parse import quote

import psycopg
from psycopg import sql

gateway_root = Path(os.environ.get('GATEWAY_ROOT', '/etc/xcontrol/apisix/uat'))
prefix = Path(os.environ.get('GATEWAY_CONFIG_ROOT', gateway_root / 'config'))
runtime_root = Path(os.environ.get('GATEWAY_RUNTIME_ROOT', gateway_root / 'runtime'))
secret_file = Path(os.environ.get(
    'GATEWAY_SECRET_ENV_FILE', gateway_root / 'secrets.env'))
values = dict(os.environ)
for line in secret_file.read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key] = value.strip().strip('"').strip("'")
environment = values.get('GATEWAY_ENVIRONMENT', 'uat')

def derive(label):
    return hmac.new(seed, ('apisix-' + environment + '-v1:' + label).encode(), hashlib.sha256).hexdigest()

seed_text = values.get('INTERNAL_SERVICE_TOKEN', '').strip()
if seed_text:
    seed = seed_text.encode()
    if len(seed) < 24:
        raise SystemExit('INTERNAL_SERVICE_TOKEN is too short')
    config_dsn = ''
    admin_token = derive('admin')
    node_token = ''
else:
    # Existing Vault-injected runtime credentials are valid for restarts and
    # for hosts whose database migration was completed by the control plane.
    config_dsn = values.get('GATEWAY_PG_DSN', '').strip()
    admin_token = values.get('GATEWAY_ADMIN_TOKEN', '').strip()
    node_token = values.get('GATEWAY_NODE_TOKEN', '').strip()
    if not config_dsn or len(admin_token) < 32 or len(node_token) < 32:
        raise SystemExit('runtime gateway credentials are incomplete')

if seed_text:
    schema = prefix.joinpath('schema.sql').read_text()
    password = derive('database')
    migration_dsn = values.get('GATEWAY_MIGRATION_DSN', '').strip()
    database_user = 'gateway_config_api'
    database_name = values.get('GATEWAY_CONFIG_DB_NAME', 'account')
    database_host = values.get('GATEWAY_CONFIG_DB_HOST', '').strip()
    database_port = values.get('GATEWAY_CONFIG_DB_PORT', '5432')
    if migration_dsn and not database_host:
        raise SystemExit('GATEWAY_CONFIG_DB_HOST is required with migration DSN')
    schema_sql = """BEGIN;
DO $$ BEGIN
IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='gateway_config_api') THEN
CREATE ROLE gateway_config_api LOGIN;
END IF;
END $$;
ALTER ROLE gateway_config_api PASSWORD {password};
{schema}
GRANT USAGE ON SCHEMA gateway_config TO gateway_config_api;
GRANT SELECT, INSERT ON gateway_config.revisions, gateway_config.audit TO gateway_config_api;
GRANT SELECT, INSERT, UPDATE ON gateway_config.publications, gateway_config.node_status TO gateway_config_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gateway_config TO gateway_config_api;
COMMIT;
"""
    if migration_dsn:
        for attempt in range(30):
            try:
                with psycopg.connect(migration_dsn, connect_timeout=5,
                                     options='-c statement_timeout=10000') as db:
                    db.execute(sql.SQL(schema_sql).format(
                        password=sql.Literal(password), schema=sql.SQL(schema)))
                break
            except Exception:
                if attempt == 29:
                    raise SystemExit(
                        'Gateway database preparation failed; secret-bearing SQL output suppressed')
                time.sleep(2)
    if not config_dsn:
        config_dsn = 'postgresql://{}:{}@{}:{}/{}'.format(
            quote(database_user, safe=''), quote(password, safe=''),
            database_host, database_port, database_name)
    node_token = derive('node:' + values.get('GATEWAY_NODE_ID', values.get('GATEWAY_GATEWAY_ID', 'gateway')))

node_id = values.get('GATEWAY_NODE_ID', values.get('GATEWAY_GATEWAY_ID', 'gateway'))
runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
def write_runtime(name, content):
    path = runtime_root / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(content)

nodes_file = Path(values.get('GATEWAY_NODE_TOKENS_FILE', runtime_root / 'nodes.json'))
if not nodes_file.exists():
    nodes_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(nodes_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(json.dumps({node_id: node_token}))
write_runtime('api.env', 'GATEWAY_PG_DSN=' + config_dsn + '\n'
      + 'GATEWAY_ADMIN_TOKEN=' + admin_token + '\n'
      + 'GATEWAY_NODE_TOKENS_FILE=' + str(nodes_file) + '\n')
write_runtime('sync.env', 'GATEWAY_NODE_TOKEN=' + node_token + '\n')
print('APISIX ' + environment + ' schema and runtime credentials ready')
