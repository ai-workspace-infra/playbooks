#!/usr/bin/env bash
set -euo pipefail

# Merge the Accounts domain into an already-initialized Supabase target.
# Unlike the public-schema replacement path, this never drops target tables and
# delegates conflict semantics to the Accounts service's migratectl:
#   export (inside the PROD PostgreSQL container, loopback trust)
#   import --merge --merge-strategy timestamp (against Supabase)

TARGET_DSN="${SUPABASE_TARGET_DSN:-}"
EXPECTED_REF="${SUPABASE_EXPECTED_PROJECT_REF:-}"
VAULT_REF="${SUPABASE_VAULT_PROJECT_REF:-}"
DRY_RUN="${SUPABASE_METADATA_DRY_RUN:-true}"
MODE="${SUPABASE_MIGRATION_MODE:-metadata_and_data}"
CONNECTION_MODE="${SUPABASE_TARGET_CONNECTION_MODE:-session_pooler}"
SOURCE_BACKEND="${SUPABASE_SOURCE_BACKEND:-supabase}"
[[ "${VAULT_ENV_PATH:-}" == "uat" ]] || { echo "ERROR: Accounts merge target must be UAT." >&2; exit 1; }
SOURCE_DSN="${SUPABASE_SOURCE_DSN:-}"
SOURCE_SSH_HOST="${SUPABASE_SOURCE_TUNNEL_HOST:-}"
SOURCE_SSH_USER="${SUPABASE_SOURCE_SSH_USER:-root}"
SOURCE_SSH_KEY_PATH="${SUPABASE_SOURCE_SSH_KEY_PATH:-${HOME}/.ssh/id_deploy}"
SOURCE_CONTAINER="${SUPABASE_SOURCE_DB_CONTAINER:-postgresql-svc-plus}"
SOURCE_DB_USER="${SUPABASE_SOURCE_DB_USER:-readonly}"
SOURCE_DB_NAME="${SUPABASE_SOURCE_DATABASE:-account}"
MIGRATECTL_BIN="${MIGRATECTL_BIN:-}"
RUNTIME_IMAGE="${SUPABASE_MIGRATION_RUNTIME_IMAGE:-alpine:3.20}"
SNAPSHOT_FILE="${SUPABASE_ACCOUNTS_SNAPSHOT_FILE:-${RUNNER_TEMP:-/tmp}/supabase-accounts-snapshot.yaml}"
BACKUP_FILE="${SUPABASE_TARGET_BACKUP_FILE:-${RUNNER_TEMP:-/tmp}/supabase-accounts-merge-backup.sql}"
REMOTE_DIR="/root/.supabase-accounts-merge.$$"

redact_dsn() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

SSH_OPTIONS=(
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=15
  -i "${SOURCE_SSH_KEY_PATH}"
)

cleanup() {
  if [[ -n "${SOURCE_SSH_HOST}" && "${SOURCE_BACKEND}" == "vps" ]]; then
    ssh "${SSH_OPTIONS[@]}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" \
      "rm -rf $(printf '%q' "${REMOTE_DIR}")" >/dev/null 2>&1 || true
  fi
  rm -f "${SNAPSHOT_FILE}"
}
trap cleanup EXIT

case "${SOURCE_BACKEND}" in
  vps|supabase) ;;
  *)
    echo "ERROR: Unsupported source backend: ${SOURCE_BACKEND}. Allowed: vps, supabase." >&2
    exit 1
    ;;
esac

if [[ -z "${TARGET_DSN}" || -z "${MIGRATECTL_BIN}" ]]; then
  echo "ERROR: target DSN and MIGRATECTL_BIN are required." >&2
  exit 1
fi
if [[ ! -x "${MIGRATECTL_BIN}" ]]; then
  echo "ERROR: MIGRATECTL_BIN is not executable: ${MIGRATECTL_BIN}" >&2
  exit 1
fi
if [[ "${MODE}" != "metadata_and_data" ]]; then
  echo "ERROR: Accounts merge requires SUPABASE_MIGRATION_MODE=metadata_and_data." >&2
  exit 1
fi

if [[ "${SOURCE_BACKEND}" == "supabase" ]]; then
  if [[ -z "${SOURCE_DSN}" ]]; then
    echo "ERROR: source Supabase DSN (SUPABASE_SOURCE_DSN) is required when SOURCE_BACKEND=supabase." >&2
    exit 1
  fi
  if [[ "${SOURCE_DSN}" == "${TARGET_DSN}" ]]; then
    echo "ERROR: source DSN and target DSN must not be identical." >&2
    exit 1
  fi
elif [[ "${SOURCE_BACKEND}" == "vps" ]]; then
  if [[ -z "${SOURCE_SSH_HOST}" ]]; then
    echo "ERROR: PROD source SSH host is required when SOURCE_BACKEND=vps." >&2
    exit 1
  fi
  if [[ ! -r "${SOURCE_SSH_KEY_PATH}" ]]; then
    echo "ERROR: source PostgreSQL SSH key is missing: ${SOURCE_SSH_KEY_PATH}" >&2
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
  command -v ssh >/dev/null || { echo "ERROR: ssh is required." >&2; exit 1; }
  command -v scp >/dev/null || { echo "ERROR: scp is required." >&2; exit 1; }
fi
if [[ "${TARGET_DSN}" != *"supabase.com"* || "${TARGET_DSN}" == *"svc.plus"* ]]; then
  echo "ERROR: Accounts merge target must be a Supabase DSN, never a PROD platform DSN." >&2
  exit 1
fi
if [[ "${TARGET_DSN}" =~ :6543([/?]|$) ]]; then
  echo "ERROR: transaction pooler (port 6543) is not supported for migratectl merge." >&2
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
command -v psql >/dev/null || { echo "ERROR: psql is required to inspect the target server version." >&2; exit 1; }

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

run_remote() {
  local remote_command
  printf -v remote_command '%q ' "$@"
  ssh "${SSH_OPTIONS[@]}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" "${remote_command}"
}

echo "Supabase Accounts domain merge (PROD -> UAT)"
if [[ "${SOURCE_BACKEND}" == "supabase" ]]; then
  echo "  source: $(redact_dsn "${SOURCE_DSN}") (Supabase)"
else
  echo "  source: ${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}/${SOURCE_CONTAINER}:${SOURCE_DB_NAME} (role ${SOURCE_DB_USER})"
fi
echo "  target: $(redact_dsn "${TARGET_DSN}")"
echo "  dry-run: ${DRY_RUN}"

if [[ "${SOURCE_BACKEND}" == "supabase" ]]; then
  echo "[1/4] Exporting Accounts snapshot directly from source Supabase..."
  "${MIGRATECTL_BIN}" export \
    --dsn "${SOURCE_DSN}" \
    --output "${SNAPSHOT_FILE}"
else
  echo "[1/4] Exporting Accounts snapshot inside PROD PostgreSQL container..."
  run_remote mkdir -p "${REMOTE_DIR}"
  run_remote chmod 700 "${REMOTE_DIR}"
  scp "${SSH_OPTIONS[@]}" -q "${MIGRATECTL_BIN}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}:${REMOTE_DIR}/migratectl"
  run_remote chmod 700 "${REMOTE_DIR}/migratectl"

  if ! run_remote docker image inspect "${RUNTIME_IMAGE}" >/dev/null 2>&1; then
    echo "  pulling source runtime image: ${RUNTIME_IMAGE}"
    run_remote docker pull -q "${RUNTIME_IMAGE}" >/dev/null
  fi

  remote_export=(
    docker run --rm
    --network "container:${SOURCE_CONTAINER}"
    -v "${REMOTE_DIR}:/work"
    "${RUNTIME_IMAGE}"
    /work/migratectl export
    --dsn "postgres://${SOURCE_DB_USER}@127.0.0.1:5432/${SOURCE_DB_NAME}?sslmode=disable"
    --output /work/snapshot.yaml
  )
  run_remote "${remote_export[@]}"
  ssh "${SSH_OPTIONS[@]}" "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" \
    "cat $(printf '%q' "${REMOTE_DIR}/snapshot.yaml")" >"${SNAPSHOT_FILE}"
fi
[[ -s "${SNAPSHOT_FILE}" ]] || { echo "ERROR: Accounts snapshot is empty." >&2; exit 1; }

echo "[2/4] Running Accounts merge dry-run against Supabase..."
merge_args=(
  import
  --dsn "${TARGET_DSN}"
  --file "${SNAPSHOT_FILE}"
  --regenerate-user-uuids
  --dry-run
  --merge
  --merge-strategy timestamp
  --preserve-existing-users
  --skip-sessions
)
preview_output="$("${MIGRATECTL_BIN}" "${merge_args[@]}" 2>&1)" || { echo "ERROR: Accounts merge dry-run failed; inspect target/source compatibility privately." >&2; exit 1; }
echo "${preview_output}" | grep -E '^(Import preview:|Identities |Sessions |Conflicts )' || true
if ! grep -qE '^Import preview: users inserted=[0-9]+ updated=0 ' <<<"${preview_output}" ||
   ! grep -qE '^Identities inserted=[0-9]+ updated=[0-9]+ deleted=0$' <<<"${preview_output}" ||
   ! grep -qE '^Sessions inserted=0 updated=0 deleted=0$' <<<"${preview_output}"; then
  echo "ERROR: Accounts merge preview would change existing user profiles, delete identities, or import sessions." >&2
  exit 1
fi

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[3/4] DRY_RUN=true; no Supabase data writes executed."
  echo "[4/4] Accounts merge preflight completed."
  exit 0
fi

if grep -qE '^Import preview: users inserted=0 updated=0 ' <<<"${preview_output}" &&
   grep -qE '^Identities inserted=0 updated=0 deleted=0$' <<<"${preview_output}" &&
   grep -qE '^Sessions inserted=0 updated=0 deleted=0$' <<<"${preview_output}"; then
  echo "[3/4] PROD→UAT Accounts source and target already converged; no UAT writes or credential-containing backup needed."
  echo "[4/4] Accounts merge completed as a verified no-op."
  exit 0
fi

echo "[3/4] Backing up existing Supabase public schema/data before merge..."
target_pg_dump "${BACKUP_FILE}" \
  --schema=public \
  --no-owner \
  --no-privileges \
  --no-publications \
  --no-subscriptions
[[ -s "${BACKUP_FILE}" ]] || { echo "ERROR: target backup is empty; refusing Accounts merge." >&2; exit 1; }

echo "[3/4] Applying Accounts merge with timestamp conflict resolution..."
merge_args=(
  import
  --dsn "${TARGET_DSN}"
  --file "${SNAPSHOT_FILE}"
  --regenerate-user-uuids
  --merge
  --merge-strategy timestamp
  --preserve-existing-users
  --skip-sessions
)
apply_output="$("${MIGRATECTL_BIN}" "${merge_args[@]}" 2>&1)" || { echo "ERROR: Accounts merge failed; UAT backup is retained for recovery." >&2; exit 1; }
echo "${apply_output}" | grep -E '^(Import applied:|Identities |Sessions |Conflicts )' || true

echo "[4/4] Verifying Accounts merge convergence..."
verify_output="$("${MIGRATECTL_BIN}" import \
  --dsn "${TARGET_DSN}" \
  --file "${SNAPSHOT_FILE}" \
  --regenerate-user-uuids \
  --dry-run \
  --merge \
  --merge-strategy timestamp \
  --preserve-existing-users \
  --skip-sessions 2>&1)"
echo "${verify_output}" | grep -E '^(Import preview:|Identities |Sessions |Conflicts )' || true
if ! grep -qE 'users inserted=0 updated=0' <<<"${verify_output}" ||
   ! grep -qE 'Identities inserted=0 updated=0' <<<"${verify_output}" ||
   ! grep -qE 'Sessions inserted=0 updated=0' <<<"${verify_output}"; then
  echo "ERROR: Accounts merge did not converge to a no-op replay." >&2
  exit 1
fi
echo "Accounts domain merge completed for Supabase project ${EXPECTED_REF}."
