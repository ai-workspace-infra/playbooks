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

prefix = Path(os.environ.get('GATEWAY_RUNTIME_PREFIX', '/etc/xcontrol/apisix/uat'))
secret_file = Path(os.environ.get(
    'GATEWAY_SECRET_ENV_FILE', '/etc/xcontrol/apisix/uat/secrets.env'))
values = {}
for line in secret_file.read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        values[key] = value.strip().strip('"').strip("'")
seed = values['INTERNAL_SERVICE_TOKEN'].encode()
assert len(seed) >= 24

def derive(label):
    return hmac.new(seed, ('apisix-uat-v1:' + label).encode(), hashlib.sha256).hexdigest()

schema = prefix.joinpath('schema.sql').read_text()
password = derive('database')
migration_dsn = values['GATEWAY_MIGRATION_DSN']
database_user = values.get('GATEWAY_CONFIG_DB_USER', 'gateway_config_api')
database_name = values.get('GATEWAY_CONFIG_DB_NAME', 'account')
database_host = values['GATEWAY_CONFIG_DB_HOST']
database_port = values.get('GATEWAY_CONFIG_DB_PORT', '5432')
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
config_dsn = 'postgresql://{}:{}@{}:{}/{}'.format(
    quote(database_user, safe=''), quote(password, safe=''),
    database_host, database_port, database_name)

def write(name, content):
    path = prefix / 'runtime' / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(content)

write('nodes.json', json.dumps({'web-saas-uat':derive('node:web-saas-uat')}))
write('api.env', 'GATEWAY_PG_DSN=' + config_dsn + '\n'
      + 'GATEWAY_ADMIN_TOKEN=' + derive('admin') + '\n'
      + 'GATEWAY_NODE_TOKENS_FILE=' + str(prefix / 'runtime' / 'nodes.json') + '\n')
write('sync.env', 'GATEWAY_NODE_TOKEN=' + derive('node:web-saas-uat') + '\n')
print('APISIX UAT schema and runtime credentials ready')
