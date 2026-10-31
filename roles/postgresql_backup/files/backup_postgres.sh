#!/bin/bash
set -euo pipefail

# ==============================================================================
# PostgreSQL Full High-Strength Encrypted S3 Backup Script
# ==============================================================================

# Configurations (can be overridden by Env Vars)
BACKUP_DIR="${BACKUP_DIR:-/tmp/pg_backup_staging}"
DATE=$(date +"%Y%m%d_%H%M%S")
ENCRYPTION_PASS="${BACKUP_ENCRYPTION_PASS:-}"
S3_BUCKET="${S3_BUCKET:-}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_REGION="${S3_REGION:-}"
S3_PREFIX="${S3_PREFIX:-postgres-backups}"

VAULT_ADDR="${VAULT_ADDR:-https://vault.svc.plus}"
VAULT_TOKEN="${VAULT_TOKEN:-}"
VAULT_ROLE="${VAULT_ROLE:-}"
VAULT_JWT="${VAULT_JWT:-}"
VAULT_IAC_STATE_PATH="${VAULT_IAC_STATE_PATH:-kv/data/CICD/${VAULT_ENV_PATH:-prod}/iac_state}"
export VAULT_IAC_STATE_PATH

# Validation
if [ -z "$ENCRYPTION_PASS" ]; then
    echo "[ERROR] BACKUP_ENCRYPTION_PASS environment variable is not set." >&2
    exit 1
fi

# Fallback token locations
if [ -z "$VAULT_TOKEN" ] && [ -f ~/.vault-token ]; then
    VAULT_TOKEN=$(cat ~/.vault-token)
fi
if [ -z "$VAULT_TOKEN" ] && [ -f ~/.ai_workspace_auth_token ]; then
    VAULT_TOKEN=$(cat ~/.ai_workspace_auth_token)
fi

# Fetch S3 secrets from Vault if not explicitly set in Env
if [ -z "$S3_BUCKET" ] || [ -z "$S3_ACCESS_KEY" ] || [ -z "$S3_SECRET_KEY" ]; then
    echo "[INFO] S3 environment variables not complete. Fetching from Vault kv/CICD..."
    
    # Inline python helper to fetch S3 credentials from Vault
    S3_DATA=$(python3 -c "
import urllib.request, urllib.parse, json, os, sys

vault_addr = '$VAULT_ADDR'
token = '$VAULT_TOKEN'
jwt = '$VAULT_JWT'
role = '$VAULT_ROLE'

# Try to login via JWT first if JWT is provided
if jwt and role:
    login_url = f'{vault_addr}/v1/auth/jwt/login'
    login_data = json.dumps({'jwt': jwt, 'role': role}).encode('utf-8')
    req = urllib.request.Request(login_url, data=login_data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            token = json.loads(resp.read().decode())['auth']['client_token']
    except Exception as e:
        print(f'JWT Login failed: {e}', file=sys.stderr)
        sys.exit(1)

if not token:
    print('Error: Vault token/JWT not provided.', file=sys.stderr)
    sys.exit(1)

# Fetch the environment-scoped Terraform state contract.
state_path = os.environ.get('VAULT_IAC_STATE_PATH', 'kv/data/CICD/prod/iac_state')
req = urllib.request.Request(f"{vault_addr}/v1/{state_path}", headers={'X-Vault-Token': token})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())['data']['data']
        print(json.dumps(data))
except Exception as e:
    print(f'Failed to read Vault {state_path}: {e}', file=sys.stderr)
    sys.exit(1)
")
    
    # Parse S3 configuration
    S3_BUCKET=$(echo "$S3_DATA" | jq -r '.TF_STATE_BUCKET // empty')
    S3_ACCESS_KEY=$(echo "$S3_DATA" | jq -r '.TF_STATE_ACCESS_KEY // empty')
    S3_SECRET_KEY=$(echo "$S3_DATA" | jq -r '.TF_STATE_SECRET_KEY // empty')
    S3_ENDPOINT=$(echo "$S3_DATA" | jq -r '.TF_STATE_ENDPOINT // empty')
    S3_REGION=$(echo "$S3_DATA" | jq -r '.TF_STATE_REGION // empty')
fi

# Final validation on S3 credentials
if [ -z "$S3_BUCKET" ] || [ -z "$S3_ACCESS_KEY" ] || [ -z "$S3_SECRET_KEY" ]; then
    echo "[ERROR] S3 credentials are missing or could not be retrieved from Vault." >&2
    exit 1
fi

# Export S3 credentials so aws-cli automatically uses them
export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}"

mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

echo "[INFO] [$(date)] Starting full backup of postgresql-svc-plus..."

# 1. Get database list dynamically
DB_LIST=$(docker exec postgresql-svc-plus psql -U postgres -t -c "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres';")

# 2. Backup databases individually
for DB in $DB_LIST; do
    echo "[INFO] Backing up database: $DB..."
    raw_dump="${DB}_${DATE}.sql.gz"
    enc_dump="${raw_dump}.enc"
    
    # Logical dump & compression
    docker exec postgresql-svc-plus pg_dump -U postgres "$DB" | gzip > "$raw_dump"
    
    # High-strength symmetric encryption
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 -pass pass:"$ENCRYPTION_PASS" -in "$raw_dump" -out "$enc_dump"
    
    # Upload to S3
    s3_path="s3://${S3_BUCKET}/${S3_PREFIX}/${DATE}/${enc_dump}"
    s3_opts=""
    if [ -n "$S3_ENDPOINT" ]; then
        s3_opts="--endpoint-url ${S3_ENDPOINT}"
    fi
    aws s3 cp "$enc_dump" "$s3_path" $s3_opts
    
    rm -f "$raw_dump" "$enc_dump"
done

# 3. Dump cluster globals (roles/permissions)
echo "[INFO] Backing up global cluster definitions..."
raw_globals="globals_${DATE}.sql.gz"
enc_globals="${raw_globals}.enc"
docker exec postgresql-svc-plus pg_dumpall -U postgres --globals-only | gzip > "$raw_globals"
openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 -pass pass:"$ENCRYPTION_PASS" -in "$raw_globals" -out "$enc_globals"
s3_path="s3://${S3_BUCKET}/${S3_PREFIX}/${DATE}/${enc_globals}"
aws s3 cp "$enc_globals" "$s3_path" ${S3_ENDPOINT:+--endpoint-url $S3_ENDPOINT}
rm -f "$raw_globals" "$enc_globals"

rm -rf "$BACKUP_DIR"
echo "[INFO] [$(date)] Full database backup completed successfully."
