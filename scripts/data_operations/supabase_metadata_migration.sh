#!/usr/bin/env bash
set -euo pipefail

# Export the current PostgreSQL public-schema metadata and, when requested,
# business rows to the configured Supabase project. This is deliberately
# separate from migratectl: migratectl moves Accounts rows between VPS hosts,
# while this path is the one-way VPS -> Supabase database cutover path.
# The target must use Supabase's direct endpoint or session pooler, never a
# transaction pooler URL, because pg_dump/DDL need a stable session. The source
# dump runs inside the PROD PostgreSQL container over SSH: the container-local
# pg_hba trust rule avoids putting a stale database password on the runner.

TARGET_DSN="${SUPABASE_TARGET_DSN:-}"
EXPECTED_REF="${SUPABASE_EXPECTED_PROJECT_REF:-}"
VAULT_REF="${SUPABASE_VAULT_PROJECT_REF:-}"
DRY_RUN="${SUPABASE_METADATA_DRY_RUN:-true}"
MODE="${SUPABASE_MIGRATION_MODE:-metadata}"
CONNECTION_MODE="${SUPABASE_TARGET_CONNECTION_MODE:-session_pooler}"
TARGET_STRATEGY="${SUPABASE_TARGET_EXISTING_STRATEGY:-reject}"
CONFIRM_REPLACE="${SUPABASE_TARGET_CONFIRM_REPLACE:-false}"
SOURCE_SSH_HOST="${SUPABASE_SOURCE_TUNNEL_HOST:-}"
# This migration is deliberately SSH-only: console.svc.plus publishes
# PostgreSQL only on its loopback address, never to GitHub-hosted runners.
SOURCE_SSH_USER="${SUPABASE_SOURCE_SSH_USER:-root}"
SOURCE_SSH_KEY_PATH="${SUPABASE_SOURCE_SSH_KEY_PATH:-${HOME}/.ssh/id_deploy}"
SOURCE_CONTAINER="${SUPABASE_SOURCE_DB_CONTAINER:-postgresql-svc-plus}"
SOURCE_DB_USER="${SUPABASE_SOURCE_DB_USER:-readonly}"
SOURCE_DB_NAME="${SUPABASE_SOURCE_DATABASE:-account}"
DUMP_DIR="${SUPABASE_METADATA_DUMP_DIR:-${RUNNER_TEMP:-/tmp}/supabase-metadata-migration}"
SCHEMA_FILE="${DUMP_DIR}/public-schema.sql"
DATA_FILE="${DUMP_DIR}/public-data.sql"
APPLY_FILE="${DUMP_DIR}/apply.sql"
BACKUP_FILE="${SUPABASE_TARGET_BACKUP_FILE:-${RUNNER_TEMP:-/tmp}/supabase-public-backup.sql}"
cleanup() {
  rm -rf "${DUMP_DIR}"
}
trap cleanup EXIT

redact_dsn() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

if [[ -z "${TARGET_DSN}" ]]; then
  echo "ERROR: Supabase target DSN is required from Vault." >&2
  exit 1
fi
if [[ -z "${VAULT_REF}" ]]; then
  echo "ERROR: Supabase Vault is missing PROJECT_REF." >&2
  exit 1
fi
if [[ -z "${EXPECTED_REF}" ]]; then
  EXPECTED_REF="$(printf '%s' "${TARGET_DSN}" | sed -nE 's#.*postgres\.([a-z0-9]+):.*@.*#\1#p')"
  if [[ -z "${EXPECTED_REF}" ]]; then
    EXPECTED_REF="$(printf '%s' "${TARGET_DSN}" | sed -nE 's#.*db\.([a-z0-9]+)\.supabase\.co.*#\1#p')"
  fi
fi
if [[ -z "${EXPECTED_REF}" || "${VAULT_REF}" != "${EXPECTED_REF}" ]]; then
  echo "ERROR: Supabase Vault PROJECT_REF does not match the requested project ref." >&2
  exit 1
fi
if [[ "${TARGET_DSN}" != *"supabase.com"* ]]; then
  echo "ERROR: target DSN is not a Supabase endpoint; refusing to write." >&2
  exit 1
fi
if [[ "${CONNECTION_MODE}" != "session_pooler" && "${CONNECTION_MODE}" != "direct" ]]; then
  echo "ERROR: SUPABASE_TARGET_CONNECTION_MODE must be session_pooler or direct." >&2
  exit 1
fi
if [[ "${TARGET_DSN}" == *"sslmode=disable"* ]]; then
  echo "ERROR: Supabase target connection must use TLS." >&2
  exit 1
fi
if [[ "${TARGET_DSN}" =~ :6543([/?]|$) ]]; then
  echo "ERROR: transaction pooler (port 6543) is not supported for pg_dump/DDL." >&2
  exit 1
fi
if [[ "${CONNECTION_MODE}" == "session_pooler" && "${TARGET_DSN}" != *"pooler.supabase.com"* ]]; then
  echo "ERROR: session_pooler mode requires a Supavisor session-pooler DSN." >&2
  exit 1
fi
if [[ "${CONNECTION_MODE}" == "direct" && "${TARGET_DSN}" == *"pooler.supabase.com"* ]]; then
  echo "ERROR: direct mode cannot use a Supabase pooler DSN." >&2
  exit 1
fi
if [[ "${TARGET_DSN}" == *"svc.plus"* ]]; then
  echo "ERROR: target DSN must not point at a platform PROD host; refusing to write." >&2
  exit 1
fi
if [[ "${MODE}" != "metadata" && "${MODE}" != "metadata_and_data" ]]; then
  echo "ERROR: SUPABASE_MIGRATION_MODE must be metadata or metadata_and_data." >&2
  exit 1
fi
if [[ "${TARGET_STRATEGY}" != "reject" && "${TARGET_STRATEGY}" != "replace_public" ]]; then
  echo "ERROR: SUPABASE_TARGET_EXISTING_STRATEGY must be reject or replace_public for the schema migration." >&2
  exit 1
fi
if [[ "${TARGET_STRATEGY}" == "replace_public" && "${DRY_RUN}" != "true" && "${CONFIRM_REPLACE}" != "true" ]]; then
  echo "ERROR: replace_public requires SUPABASE_TARGET_CONFIRM_REPLACE=true." >&2
  echo "       This explicit confirmation is required before clearing the target public schema." >&2
  exit 1
fi
if [[ -z "${SOURCE_SSH_HOST}" ]]; then
    echo "ERROR: SUPABASE_SOURCE_TUNNEL_HOST is not configured." >&2
    echo "       Configure the PROD PostgreSQL SSH host." >&2
    exit 1
fi
if [[ "${SOURCE_DB_USER}" != "readonly" ]]; then
    echo "ERROR: source DB role must remain readonly; refusing to run as ${SOURCE_DB_USER}." >&2
    exit 1
fi
if [[ ! "${SOURCE_CONTAINER}" =~ ^[a-zA-Z0-9_.-]+$ || ! "${SOURCE_DB_NAME}" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
    echo "ERROR: source container and database names contain unsafe characters." >&2
    exit 1
fi
if [[ ! -r "${SOURCE_SSH_KEY_PATH}" ]]; then
    echo "ERROR: source PostgreSQL SSH key is missing: ${SOURCE_SSH_KEY_PATH}" >&2
    echo "       Configure MIGRATION_SOURCE_SSH_PRIVATE_KEY_B64 before starting the export." >&2
    exit 1
fi
command -v ssh >/dev/null || { echo "ERROR: ssh is required for the source export." >&2; exit 1; }
SSH_OPTIONS=(
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=15
  -o ServerAliveInterval=30
  -i "${SOURCE_SSH_KEY_PATH}"
)

remote_pg_dump() {
  local output_file="$1"
  shift
  local -a command=(docker exec "${SOURCE_CONTAINER}" pg_dump -U "${SOURCE_DB_USER}" -d "${SOURCE_DB_NAME}" "$@")
  local remote_command
  printf -v remote_command '%q ' "${command[@]}"
  ssh "${SSH_OPTIONS[@]}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" "${remote_command}" >"${output_file}"
}

source_probe_command=(docker exec "${SOURCE_CONTAINER}" pg_isready -U "${SOURCE_DB_USER}" -d "${SOURCE_DB_NAME}")
printf -v source_probe '%q ' "${source_probe_command[@]}"
if ! ssh "${SSH_OPTIONS[@]}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" "${source_probe}" >/dev/null; then
  echo "ERROR: PROD PostgreSQL container is not ready: ${SOURCE_CONTAINER}/${SOURCE_DB_NAME}." >&2
  exit 1
fi
echo "Source PostgreSQL container ready: ${SOURCE_SSH_HOST}/${SOURCE_CONTAINER}:${SOURCE_DB_NAME}"
command -v psql >/dev/null || { echo "ERROR: psql is required." >&2; exit 1; }
target_pg_dump() {
  local output_file="$1"
  shift
  local output_dir output_name server_major client_major
  output_dir="$(cd "$(dirname "${output_file}")" && pwd)"
  output_name="$(basename "${output_file}")"
  server_major="$(psql "${TARGET_DSN}" -Atqc "SELECT current_setting('server_version_num')::int / 10000;")"
  client_major="$(pg_dump --version 2>/dev/null | awk '{print $3}' | cut -d. -f1 || true)"
  if [[ "${client_major}" =~ ^[0-9]+$ && "${client_major}" -ge "${server_major}" ]]; then
    pg_dump "${TARGET_DSN}" "$@" --file="${output_file}"
    return
  fi
  command -v docker >/dev/null || {
    echo "ERROR: pg_dump ${client_major:-unknown} cannot back up PostgreSQL ${server_major}, and Docker is unavailable for a matching client." >&2
    exit 1
  }
  echo "  using postgres:${server_major} client for PostgreSQL ${server_major} target"
  docker run --rm --pull=missing \
    -e TARGET_DSN="${TARGET_DSN}" \
    -v "${output_dir}:/backup" \
    "postgres:${server_major}" \
    pg_dump "${TARGET_DSN}" "$@" --file="/backup/${output_name}"
}
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "ERROR: sha256sum or shasum is required." >&2
    exit 1
  fi
}
mkdir -p "${DUMP_DIR}"

echo "Supabase one-way database migration"
echo "  source: ${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}/${SOURCE_CONTAINER}:${SOURCE_DB_NAME} (role ${SOURCE_DB_USER})"
echo "  target: $(redact_dsn "${TARGET_DSN}")"
echo "  project: ${EXPECTED_REF}"
echo "  connection: ${CONNECTION_MODE}"
echo "  mode: ${MODE}"
echo "  existing-target strategy: ${TARGET_STRATEGY}"
echo "  dry-run: ${DRY_RUN}"

echo "[1/5] Exporting public-schema metadata only..."
remote_pg_dump "${SCHEMA_FILE}" \
  --schema-only \
  --schema=public \
  --no-owner \
  --no-privileges \
  --no-comments \
  --no-publications \
  --no-subscriptions \
  --file=-

if [[ ! -s "${SCHEMA_FILE}" ]]; then
  echo "ERROR: schema dump is empty." >&2
  exit 1
fi
if grep -Eq 'CREATE EXTENSION[^;]*pglogical|CREATE SCHEMA[^;]*pglogical' "${SCHEMA_FILE}"; then
  echo "ERROR: source dump contains pglogical objects; Supabase Cloud does not accept this replication extension." >&2
  exit 1
fi
for object in 'CREATE TABLE.*public\.users'; do
  if ! grep -Eiq "${object}" "${SCHEMA_FILE}"; then
    echo "ERROR: required Accounts metadata is missing from the source dump: ${object}" >&2
    exit 1
  fi
done

schema_sha256="$(hash_file "${SCHEMA_FILE}")"
data_sha256="none"
if [[ "${MODE}" == "metadata_and_data" ]]; then
  echo "[2/5] Exporting public-schema business data only..."
  remote_pg_dump "${DATA_FILE}" \
    --data-only \
    --schema=public \
    --no-owner \
    --no-privileges \
    --no-comments \
    --no-publications \
    --no-subscriptions \
    --file=-
  if [[ ! -s "${DATA_FILE}" ]]; then
    echo "ERROR: business data dump is empty." >&2
    exit 1
  fi
  data_sha256="$(hash_file "${DATA_FILE}")"
else
  echo "[2/5] Metadata-only mode; business rows will not be exported."
fi

echo "[3/5] Target preflight (read-only)..."
existing_tables="$(psql "${TARGET_DSN}" -Atqc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name NOT IN ('spatial_ref_sys');")"
echo "  existing public tables: ${existing_tables}"
echo "  schema sha256: ${schema_sha256}"
echo "  data sha256: ${data_sha256}"

expected_marker="${schema_sha256}:${data_sha256}"
existing_marker="$(psql "${TARGET_DSN}" -Atqc "SELECT COALESCE((SELECT source_schema_sha256 || ':' || source_data_sha256 FROM public.platform_schema_migration_markers WHERE component = 'accounts-billing-public-schema'), '') FROM (SELECT 1) AS probe;" 2>/dev/null || true)"
if [[ -n "${existing_marker}" ]]; then
  if [[ "${existing_marker}" == "${expected_marker}" ]]; then
    echo "  migration marker matches the source dump; target is already converged."
    exit 0
  fi
  if [[ "${TARGET_STRATEGY}" == "reject" ]]; then
    echo "ERROR: target migration marker exists but does not match the source dump." >&2
    exit 1
  fi
  echo "  migration marker differs; replace_public will rebuild the public schema."
fi

if [[ "${TARGET_STRATEGY}" == "replace_public" && "${existing_tables}" != "0" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[4/5] DRY_RUN=true; replace_public would back up and clear the existing public schema."
    echo "[5/5] Supabase metadata/data preflight completed without target writes."
    exit 0
  fi

  command -v pg_dump >/dev/null || { echo "ERROR: pg_dump is required to back up the target before replace_public." >&2; exit 1; }
  echo "[4/5] Backing up existing public schema/data before replace_public..."
  target_pg_dump "${BACKUP_FILE}" \
    --schema=public \
    --no-owner \
    --no-privileges \
    --no-publications \
    --no-subscriptions
  [[ -s "${BACKUP_FILE}" ]] || { echo "ERROR: target backup is empty; refusing to clear public schema." >&2; exit 1; }
  echo "  target backup: ${BACKUP_FILE}"
  echo "  clearing existing public objects (Supabase-managed auth/storage schemas are untouched)..."
  psql "${TARGET_DSN}" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
  object_record RECORD;
BEGIN
  FOR object_record IN
    SELECT n.nspname AS schema_name,
           c.relname AS object_name,
           CASE c.relkind
             WHEN 'r' THEN 'TABLE'
             WHEN 'p' THEN 'TABLE'
             WHEN 'v' THEN 'VIEW'
             WHEN 'm' THEN 'MATERIALIZED VIEW'
             WHEN 'S' THEN 'SEQUENCE'
             WHEN 'f' THEN 'FOREIGN TABLE'
           END AS object_kind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND c.relname <> 'spatial_ref_sys'
  LOOP
    EXECUTE format('DROP %s IF EXISTS %I.%I CASCADE', object_record.object_kind, object_record.schema_name, object_record.object_name);
  END LOOP;
END $$;
SQL
  existing_tables="0"
fi

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[4/5] DRY_RUN=true; no target DDL or business data writes executed."
  echo "[5/5] Supabase metadata/data preflight completed."
  exit 0
fi

if [[ "${existing_tables}" != "0" ]]; then
  echo "ERROR: target public schema is not empty; refusing an implicit overwrite." >&2
  echo "       Review the target and rerun only after an explicit migration design." >&2
  exit 1
fi

echo "[4/5] Applying ${MODE} in one transaction..."
cp "${SCHEMA_FILE}" "${APPLY_FILE}"
if [[ "${MODE}" == "metadata_and_data" ]]; then
  cat "${DATA_FILE}" >> "${APPLY_FILE}"
fi
cat >> "${APPLY_FILE}" <<SQL
CREATE TABLE IF NOT EXISTS public.platform_schema_migration_markers (
  component TEXT PRIMARY KEY,
  source_schema_sha256 TEXT NOT NULL,
  source_data_sha256 TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO public.platform_schema_migration_markers (component, source_schema_sha256, source_data_sha256)
VALUES ('accounts-billing-public-schema', '${schema_sha256}', '${data_sha256}')
ON CONFLICT (component) DO UPDATE
SET source_schema_sha256 = EXCLUDED.source_schema_sha256,
    source_data_sha256 = EXCLUDED.source_data_sha256,
    applied_at = now();
SQL
psql "${TARGET_DSN}" -v ON_ERROR_STOP=1 --single-transaction --file="${APPLY_FILE}"

echo "[5/5] One-way Supabase migration completed for project ${EXPECTED_REF}."
