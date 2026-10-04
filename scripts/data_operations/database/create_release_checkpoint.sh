#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Database Release Checkpoint Creation Engine (Supabase Cloud & VPS PostgreSQL)
# ==============================================================================
# Creates an immutable, release-tag-bound backup checkpoint before deployment/upgrade.
# Enforces In-DB ledger registration into `public.system_release_checkpoints`.

RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG is required}"
DATABASE_BACKEND="${DATABASE_BACKEND:-supabase}"
DATABASE_ENV="${DATABASE_ENV:-${VAULT_ENV_PATH:-uat}}"
DATABASE_NAME="${DATABASE_NAME:-account}"
GIT_SHA="${GIT_SHA:-$(git rev-parse HEAD 2>/dev/null || echo 'unknown')}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-${RUNNER_TEMP:-/tmp}/database-checkpoints/${RELEASE_TAG}}"
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-database-checkpoints}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_REGION="${S3_REGION:-us-east-1}"
ENCRYPTION_PASS="${BACKUP_ENCRYPTION_PASS:-}"
REQUIRE_DURABLE_CHECKPOINT="${REQUIRE_DURABLE_CHECKPOINT:-false}"
TARGET_DSN="${SUPABASE_TARGET_DSN:-${TARGET_DSN:-}}"
CONTAINER_NAME="${VPS_CONTAINER_NAME:-postgresql-svc-plus}"
WARM_LOCAL_DIR="/var/backups/checkpoints/${RELEASE_TAG}"
LEDGER_TABLE="public.system_release_checkpoints"

[[ "${REQUIRE_DURABLE_CHECKPOINT}" == "true" || "${REQUIRE_DURABLE_CHECKPOINT}" == "false" ]] || {
  echo "ERROR: REQUIRE_DURABLE_CHECKPOINT must be true or false." >&2
  exit 2
}

dump_supabase_public_schema() {
  local output_file="$1"
  local client_major
  local server_major
  client_major="$(pg_dump --version | awk '{print $3}' | cut -d. -f1)"
  server_major="$(psql "${TARGET_DSN}" -Atqc "select current_setting('server_version_num')::int / 10000" 2>/dev/null | tr -d '[:space:]')"

  if [[ -n "${server_major}" && "${client_major}" != "${server_major}" ]]; then
    if ! command -v docker >/dev/null 2>&1; then
      echo "ERROR: pg_dump ${client_major} cannot back up PostgreSQL ${server_major}, and Docker is unavailable for a matching client." >&2
      return 1
    fi
    echo "  pg_dump ${client_major} does not match PostgreSQL ${server_major}; using postgres:${server_major} client container."
    docker run --rm "postgres:${server_major}" pg_dump "${TARGET_DSN}" \
      --schema=public \
      --no-owner \
      --no-privileges \
      --no-publications \
      --no-subscriptions >"${output_file}" 2>/dev/null
    return
  fi

  pg_dump "${TARGET_DSN}" \
    --schema=public \
    --no-owner \
    --no-privileges \
    --no-publications \
    --no-subscriptions \
    --file="${output_file}" 2>/dev/null
}

mkdir -p "${CHECKPOINT_DIR}"

ensure_ledger_table() {
  local dsn="$1"
  psql "${dsn}" -v ON_ERROR_STOP=1 -Atqc "
    CREATE TABLE IF NOT EXISTS ${LEDGER_TABLE} (
        id BIGSERIAL PRIMARY KEY,
        release_tag VARCHAR(64) NOT NULL,
        environment VARCHAR(32) NOT NULL,
        database_backend VARCHAR(32) NOT NULL,
        database_name VARCHAR(64) NOT NULL,
        git_sha VARCHAR(40) NOT NULL,
        backup_s3_uri TEXT,
        local_backup_path TEXT,
        schema_hash VARCHAR(64),
        status VARCHAR(32) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_release_checkpoints_unique
        ON ${LEDGER_TABLE} (release_tag, environment, database_backend, database_name);
  " >/dev/null
}

record_ledger_entry() {
  local dsn="$1"
  local s3_uri="$2"
  local local_path="$3"
  local schema_hash="$4"
  local status="$5"
  local db_name="$6"

  psql "${dsn}" -v ON_ERROR_STOP=1 -Atqc "
    INSERT INTO ${LEDGER_TABLE}
      (release_tag, environment, database_backend, database_name, git_sha, backup_s3_uri, local_backup_path, schema_hash, status, completed_at)
    VALUES
      ('${RELEASE_TAG}', '${DATABASE_ENV}', '${DATABASE_BACKEND}', '${db_name}', '${GIT_SHA}',
       NULLIF('${s3_uri}', ''), NULLIF('${local_path}', ''), NULLIF('${schema_hash}', ''), '${status}', NOW())
    ON CONFLICT (release_tag, environment, database_backend, database_name)
    DO UPDATE SET
      status = EXCLUDED.status,
      git_sha = EXCLUDED.git_sha,
      backup_s3_uri = COALESCE(EXCLUDED.backup_s3_uri, ${LEDGER_TABLE}.backup_s3_uri),
      local_backup_path = COALESCE(EXCLUDED.local_backup_path, ${LEDGER_TABLE}.local_backup_path),
      schema_hash = COALESCE(EXCLUDED.schema_hash, ${LEDGER_TABLE}.schema_hash),
      completed_at = NOW();
  " >/dev/null
}

upload_to_s3_if_configured() {
  local source_file="$1"
  local target_s3_uri="$2"

  if [[ -n "${S3_BUCKET}" && -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    echo "  uploading checkpoint to ${target_s3_uri}..."
    local s3_opts=()
    if [[ -n "${S3_ENDPOINT}" ]]; then
      s3_opts+=(--endpoint-url "${S3_ENDPOINT}")
    fi
    aws s3 cp "${source_file}" "${target_s3_uri}" "${s3_opts[@]}" >/dev/null
    return 0
  fi
  return 1
}

# ==============================================================================
# Supabase Cloud Checkpoint Handler
# ==============================================================================
checkpoint_supabase() {
  echo "Executing Supabase Cloud release checkpoint..."
  if [[ -z "${TARGET_DSN}" ]]; then
    echo "ERROR: TARGET_DSN or SUPABASE_TARGET_DSN is required for Supabase checkpoint." >&2
    exit 1
  fi
  if [[ "${TARGET_DSN}" != *"supabase.com"* && "${TARGET_DSN}" != *"localhost"* && "${TARGET_DSN}" != *"127.0.0.1"* ]]; then
    echo "ERROR: Target DSN must be a Supabase connection endpoint." >&2
    exit 1
  fi

  if [[ "${REQUIRE_DURABLE_CHECKPOINT}" == "true" ]]; then
    [[ "${DATABASE_ENV}" == "uat" && "${RELEASE_TAG}" =~ ^(uat-)?daily-build-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-r[1-9][0-9]*$ ]] || {
      echo "ERROR: Durable schema-migration checkpoints are restricted to immutable UAT snapshot tags." >&2
      exit 1
    }
    [[ -n "${S3_BUCKET}" && -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]] || {
      echo "ERROR: Durable UAT checkpoint preflight requires configured remote object storage credentials." >&2
      exit 1
    }
    [[ -n "${S3_REGION}" && -n "${S3_ENDPOINT}" && "${S3_PREFIX}" == "database-checkpoints" ]] || {
      echo "ERROR: Durable UAT checkpoint requires its UAT object-storage endpoint, region, and database-checkpoints prefix." >&2
      exit 1
    }
    [[ -n "${ENCRYPTION_PASS}" ]] || {
      echo "ERROR: Durable UAT checkpoint requires BACKUP_ENCRYPTION_PASS from the UAT-only Vault field kv/data/uat/serverless/database-backup." >&2
      exit 1
    }
    command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI is required to verify the remote checkpoint." >&2; exit 1; }
  fi

  echo "  target: Supabase connection format validated (connection details suppressed)"
  echo "  release_tag: ${RELEASE_TAG} (env=${DATABASE_ENV})"

  echo "  verifying / ensuring in-db ledger table..."
  ensure_ledger_table "${TARGET_DSN}"

  local raw_sql="${CHECKPOINT_DIR}/supabase_${DATABASE_ENV}_${RELEASE_TAG}.sql"
  local archive_file="${raw_sql}.gz"
  local enc_file="${archive_file}.enc"
  local s3_uri="s3://${S3_BUCKET:-local}/${S3_PREFIX}/${DATABASE_ENV}/supabase/${RELEASE_TAG}/$(basename "${enc_file}")"

  echo "  exporting public schema and business data via pg_dump..."
  dump_supabase_public_schema "${raw_sql}"

  [[ -s "${raw_sql}" ]] || { echo "ERROR: Checkpoint dump is empty!" >&2; exit 1; }
  local dump_size
  dump_size="$(wc -c < "${raw_sql}" | tr -d ' ')"
  echo "  logical dump created: ${dump_size} bytes"

  gzip -c "${raw_sql}" > "${archive_file}"

  if [[ -n "${ENCRYPTION_PASS}" ]]; then
    echo "  encrypting archive with AES-256-CBC..."
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
      -pass pass:"${ENCRYPTION_PASS}" -in "${archive_file}" -out "${enc_file}"
    final_artifact="${enc_file}"
  else
    final_artifact="${archive_file}"
    s3_uri="s3://${S3_BUCKET:-local}/${S3_PREFIX}/${DATABASE_ENV}/supabase/${RELEASE_TAG}/$(basename "${archive_file}")"
  fi

  local final_s3_uri=""
  local durable_object_verified="false"
  if upload_to_s3_if_configured "${final_artifact}" "${s3_uri}"; then
    final_s3_uri="${s3_uri}"
    if [[ "${REQUIRE_DURABLE_CHECKPOINT}" == "true" ]]; then
      local head_result
      local object_size
      local s3_opts=()
      [[ -z "${S3_ENDPOINT}" ]] || s3_opts+=(--endpoint-url "${S3_ENDPOINT}")
      object_size="$(aws s3api head-object --bucket "${S3_BUCKET}" --key "${s3_uri#s3://${S3_BUCKET}/}" --query ContentLength --output text "${s3_opts[@]}" 2>/dev/null)" || {
        echo "ERROR: Remote UAT checkpoint object could not be verified." >&2
        exit 1
      }
      [[ "${object_size}" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: Verified UAT checkpoint object is empty or invalid." >&2; exit 1; }
      [[ "${final_artifact}" == *.enc ]] || { echo "ERROR: Durable UAT checkpoint must be encrypted before upload." >&2; exit 1; }
      durable_object_verified="true"
    fi
  else
    [[ "${REQUIRE_DURABLE_CHECKPOINT}" != "true" ]] || {
      echo "ERROR: Durable UAT checkpoint upload failed; migration is blocked." >&2
      exit 1
    }
    echo "  S3 credentials not supplied; checkpoint preserved locally at ${final_artifact}"
  fi

  local schema_hash
  schema_hash="$(shasum -a 256 "${raw_sql}" | awk '{print $1}')"
  local artifact_sha256
  artifact_sha256="$(shasum -a 256 "${final_artifact}" | awk '{print $1}')"
  local encrypted="false"
  [[ "${final_artifact}" != *.enc ]] || encrypted="true"

  # Write manifest
  cat > "${CHECKPOINT_DIR}/manifest.json" <<EOF
{
  "release_tag": "${RELEASE_TAG}",
  "environment": "${DATABASE_ENV}",
  "backend": "supabase",
  "database_name": "postgres",
  "git_sha": "${GIT_SHA}",
  "schema_hash": "${schema_hash}",
  "backup_sha256": "${artifact_sha256}",
  "backup_file": "$(basename "${final_artifact}")",
  "encrypted": ${encrypted},
  "durable_object_verified": ${durable_object_verified},
  "dump_size_bytes": ${dump_size},
  "s3_uri": "${final_s3_uri}",
  "created_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF

  echo "  recording checkpoint into ledger..."
  record_ledger_entry "${TARGET_DSN}" "${final_s3_uri}" "${final_artifact}" "${schema_hash}" "checkpointed" "postgres"

  if [[ "${REQUIRE_DURABLE_CHECKPOINT}" == "true" && -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'durable_checkpoint_verified=true\n' >>"${GITHUB_OUTPUT}"
  fi

  rm -f "${raw_sql}"
  if [[ "${final_artifact}" == *.enc ]]; then
    rm -f "${archive_file}"
  fi
  echo "Supabase release checkpoint completed successfully for tag ${RELEASE_TAG}."
}

# ==============================================================================
# VPS PostgreSQL Checkpoint Handler
# ==============================================================================
checkpoint_vps() {
  echo "Executing VPS PostgreSQL release checkpoint..."
  command -v docker >/dev/null || { echo "ERROR: docker CLI is required for VPS checkpoint." >&2; exit 1; }

  if ! docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
    echo "ERROR: Target PostgreSQL container '${CONTAINER_NAME}' is not running." >&2
    exit 1
  fi

  echo "  container: ${CONTAINER_NAME}"
  echo "  release_tag: ${RELEASE_TAG} (env=${DATABASE_ENV})"

  local warm_dir="${WARM_LOCAL_DIR}"
  mkdir -p "${warm_dir}"
  mkdir -p "${CHECKPOINT_DIR}"

  # Get local DSN to update ledger inside container
  local local_dsn="postgres://postgres@127.0.0.1:5432/${DATABASE_NAME}?sslmode=disable"
  docker exec "${CONTAINER_NAME}" psql -U postgres -d "${DATABASE_NAME}" -Atqc "
    CREATE TABLE IF NOT EXISTS ${LEDGER_TABLE} (
        id BIGSERIAL PRIMARY KEY,
        release_tag VARCHAR(64) NOT NULL,
        environment VARCHAR(32) NOT NULL,
        database_backend VARCHAR(32) NOT NULL,
        database_name VARCHAR(64) NOT NULL,
        git_sha VARCHAR(40) NOT NULL,
        backup_s3_uri TEXT,
        local_backup_path TEXT,
        schema_hash VARCHAR(64),
        status VARCHAR(32) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_release_checkpoints_unique
        ON ${LEDGER_TABLE} (release_tag, environment, database_backend, database_name);
  " >/dev/null

  # Parse databases to back up
  IFS=',' read -ra DBS <<< "${DATABASE_NAME}"
  for db in "${DBS[@]}"; do
    echo "  exporting database ${db}..."
    local raw_dump="${CHECKPOINT_DIR}/${db}_${RELEASE_TAG}.sql.gz"
    docker exec "${CONTAINER_NAME}" pg_dump -U postgres \
      --format=plain --no-owner --no-privileges "${db}" | gzip > "${raw_dump}"

    # Copy to warm local tier
    cp -f "${raw_dump}" "${warm_dir}/"

    local final_artifact="${raw_dump}"
    local s3_uri="s3://${S3_BUCKET:-local}/${S3_PREFIX}/${DATABASE_ENV}/vps-postgres/${RELEASE_TAG}/$(basename "${raw_dump}")"
    if [[ -n "${ENCRYPTION_PASS}" ]]; then
      local enc_dump="${raw_dump}.enc"
      openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
        -pass pass:"${ENCRYPTION_PASS}" -in "${raw_dump}" -out "${enc_dump}"
      final_artifact="${enc_dump}"
      s3_uri="s3://${S3_BUCKET:-local}/${S3_PREFIX}/${DATABASE_ENV}/vps-postgres/${RELEASE_TAG}/$(basename "${enc_dump}")"
    fi

    local final_s3_uri=""
    if upload_to_s3_if_configured "${final_artifact}" "${s3_uri}"; then
      final_s3_uri="${s3_uri}"
    fi

    docker exec "${CONTAINER_NAME}" psql -U postgres -d "${db}" -Atqc "
      INSERT INTO ${LEDGER_TABLE}
        (release_tag, environment, database_backend, database_name, git_sha, backup_s3_uri, local_backup_path, status, completed_at)
      VALUES
        ('${RELEASE_TAG}', '${DATABASE_ENV}', 'vps', '${db}', '${GIT_SHA}',
         NULLIF('${final_s3_uri}', ''), '${warm_dir}/$(basename "${raw_dump}")', 'checkpointed', NOW())
      ON CONFLICT (release_tag, environment, database_backend, database_name)
      DO UPDATE SET status = 'checkpointed', completed_at = NOW();
    " >/dev/null
  done

  # Global cluster definitions
  echo "  exporting global cluster roles/permissions..."
  local globals_dump="${warm_dir}/globals_${RELEASE_TAG}.sql.gz"
  docker exec "${CONTAINER_NAME}" pg_dumpall -U postgres --globals-only | gzip > "${globals_dump}"

  # Prune warm tier: keep latest 5 tags
  echo "  pruning local warm tier (keeping latest 5 release tags)..."
  find /var/backups/checkpoints -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +6 | xargs rm -rf 2>/dev/null || true

  echo "VPS PostgreSQL release checkpoint completed successfully for tag ${RELEASE_TAG}."
}

case "${DATABASE_BACKEND}" in
  supabase)
    checkpoint_supabase
    ;;
  vps)
    checkpoint_vps
    ;;
  *)
    echo "ERROR: Unsupported DATABASE_BACKEND: ${DATABASE_BACKEND}. Allowed: supabase, vps." >&2
    exit 1
    ;;
esac
