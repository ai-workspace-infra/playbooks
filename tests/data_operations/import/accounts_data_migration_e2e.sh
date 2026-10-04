#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# End-to-end verification of the accounts PROD -> UAT one-way incremental
# migration flow documented in
#   docs/data_migration/accounts_prod_to_uat_migration_guide.md  (chapter 4)
#
# Two throwaway PostgreSQL containers stand in for PROD and UAT:
#   * PROD is seeded and locked down with a SELECT/USAGE-only `readonly` role
#     (mirrors safeguard layer 1)
#   * UAT is writable and starts with its own local-only user
#
# Verified properties:
#   V1  PROD readonly role cannot INSERT/UPDATE/DELETE
#   V2  `migratectl export` works over the readonly DSN and emits a v1 snapshot
#   V3  `migratectl import --dry-run` changes nothing in UAT
#   V4  `migratectl import --merge` inserts users/identities/sessions, preserves
#       proxy UUIDs, and remaps imported identity/session references
#   V5  re-running the import is idempotent (no duplicated rows)
#   V6  `--merge-strategy timestamp` keeps the newer UAT row on conflict
#   V7  UAT-only data is never deleted by the merge
#
# Requirements: docker, go (or a prebuilt migratectl via MIGRATECTL_BIN)
# Usage:
#   ACCOUNTS_REPO=/path/to/ai-workspace-service/accounts \
#     tests/data_operations/import/accounts_data_migration_e2e.sh
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATION_SCRIPT="${SCRIPT_DIR}/../../../scripts/data_operations/accounts_data_migration.sh"

ACCOUNTS_REPO="${ACCOUNTS_REPO:-}"
MIGRATECTL_BIN="${MIGRATECTL_BIN:-}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
PROD_CONTAINER="${PROD_CONTAINER:-acct-mig-verify-prod}"
UAT_CONTAINER="${UAT_CONTAINER:-acct-mig-verify-uat}"
PROD_PORT="${PROD_PORT:-55433}"
UAT_PORT="${UAT_PORT:-55434}"
READONLY_PASSWORD="readonly-pw"

WORKDIR="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() {
  docker rm -f "${PROD_CONTAINER}" "${UAT_CONTAINER}" >/dev/null 2>&1 || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

log()  { printf '\n=== %s ===\n' "$*"; }
ok()   { PASS=$((PASS + 1)); printf '  [PASS] %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); printf '  [FAIL] %s\n' "$*"; }
check() { # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1: expected '$2', got '$3'"; fi
}

psql_prod() { docker exec -i "${PROD_CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d account "$@"; }
psql_uat()  { docker exec -i "${UAT_CONTAINER}"  psql -v ON_ERROR_STOP=1 -U postgres -d account "$@"; }
uat_count() { psql_uat -tAc "SELECT count(*) FROM $1" | tr -d '[:space:]'; }

# ------------------------------------------------------------------------------
# 0. Toolchain
# ------------------------------------------------------------------------------
log "0. Preparing migratectl"
if [ -z "${MIGRATECTL_BIN}" ]; then
  [ -n "${ACCOUNTS_REPO}" ] || { echo "ERROR: set ACCOUNTS_REPO or MIGRATECTL_BIN" >&2; exit 2; }
  MIGRATECTL_BIN="${WORKDIR}/migratectl"
  (cd "${ACCOUNTS_REPO}" && go build -o "${MIGRATECTL_BIN}" ./cmd/migratectl)
fi
SCHEMA_SQL="${SCHEMA_SQL:-${ACCOUNTS_REPO}/sql/schema.sql}"
[ -f "${SCHEMA_SQL}" ] || { echo "ERROR: schema.sql not found at ${SCHEMA_SQL}" >&2; exit 2; }
echo "migratectl: ${MIGRATECTL_BIN}"
echo "schema:     ${SCHEMA_SQL}"

# ------------------------------------------------------------------------------
# 1. Bring up the two databases
# ------------------------------------------------------------------------------
log "1. Starting throwaway PROD/UAT PostgreSQL"
docker rm -f "${PROD_CONTAINER}" "${UAT_CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${PROD_CONTAINER}" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=account \
  -p "${PROD_PORT}:5432" "${PG_IMAGE}" >/dev/null
docker run -d --name "${UAT_CONTAINER}" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=account \
  -p "${UAT_PORT}:5432" "${PG_IMAGE}" >/dev/null

for c in "${PROD_CONTAINER}" "${UAT_CONTAINER}"; do
  for _ in $(seq 1 60); do
    if docker exec "${c}" pg_isready -U postgres -d account >/dev/null 2>&1; then break; fi
    sleep 1
  done
done
psql_prod </dev/null -c 'SELECT 1' >/dev/null
psql_uat  </dev/null -c 'SELECT 1' >/dev/null
echo "both databases ready"

log "1b. Applying accounts schema to both ends"
psql_prod -q <"${SCHEMA_SQL}" >/dev/null
psql_uat  -q <"${SCHEMA_SQL}" >/dev/null
echo "schema applied"

# ------------------------------------------------------------------------------
# 2. Seed PROD and UAT
# ------------------------------------------------------------------------------
log "2. Seeding data"
psql_prod -q <<SQL >/dev/null
INSERT INTO users (uuid, proxy_uuid, username, password, email, role, created_at, updated_at) VALUES
  ('11111111-1111-4111-8111-111111111111','aaaa1111-1111-4111-8111-111111111111','prod-alice','\$2a\$hash-alice','alice@svc.plus','user', now() - interval '10 day', now() - interval '10 day'),
  ('22222222-2222-4222-8222-222222222222','bbbb2222-2222-4222-8222-222222222222','prod-bob',  '\$2a\$hash-bob',  'bob@svc.plus',  'user', now() - interval '9 day',  now() - interval '9 day'),
  ('33333333-3333-4333-8333-333333333333','cccc3333-3333-4333-8333-333333333333','prod-carol','\$2a\$hash-carol','carol@svc.plus','user', now() - interval '8 day',  now() - interval '8 day');
INSERT INTO identities (uuid, provider, external_id, user_uuid) VALUES
  ('aaaaaaaa-1111-4111-8111-111111111111','github','gh-alice','11111111-1111-4111-8111-111111111111'),
  ('aaaaaaaa-2222-4222-8222-222222222222','github','gh-bob',  '22222222-2222-4222-8222-222222222222');
INSERT INTO sessions (uuid, token, expires_at, user_uuid) VALUES
  ('bbbbbbbb-1111-4111-8111-111111111111','tok-alice', now() + interval '7 day','11111111-1111-4111-8111-111111111111');
SQL

# UAT-only user that must survive the merge untouched (V7)
psql_uat -q <<SQL >/dev/null
INSERT INTO users (uuid, proxy_uuid, username, password, email, role, created_at, updated_at) VALUES
  ('11111111-1111-4111-8111-111111111111','aaaa1111-1111-4111-8111-111111111111','uat-legacy-alice','\$2a\$hash-legacy','alice@svc.plus','user', now(), now()),
  ('99999999-9999-4999-8999-999999999999','dddd9999-9999-4999-8999-999999999999','uat-only-tester','\$2a\$hash-uat','tester@onwalk.net','user', now(), now());
INSERT INTO identities (uuid, provider, external_id, user_uuid) VALUES
  ('aaaaaaaa-1111-4111-8111-111111111111','github','gh-alice','11111111-1111-4111-8111-111111111111');
INSERT INTO sessions (uuid, token, expires_at, user_uuid) VALUES
  ('bbbbbbbb-1111-4111-8111-111111111111','tok-alice', now() + interval '7 day','11111111-1111-4111-8111-111111111111');
INSERT INTO overlay_devices (id, user_uuid, wireguard_public_key, wireguard_address) VALUES
  ('legacy-device', '11111111-1111-4111-8111-111111111111', 'legacy-public-key', '10.99.0.10/32');
INSERT INTO overlay_config_acks (user_uuid, device_id, revision, applied_at) VALUES
  ('11111111-1111-4111-8111-111111111111', 'legacy-device', 'legacy-revision', now());
INSERT INTO subscriptions (user_uuid, provider, external_id) VALUES
  ('11111111-1111-4111-8111-111111111111', 'test', 'legacy-subscription');
SQL
echo "PROD: 3 users / 2 identities / 1 session, UAT: 1 legacy user + 1 local-only user"

# ------------------------------------------------------------------------------
# 3. Safeguard layer 1: PROD readonly role
# ------------------------------------------------------------------------------
log "3. V1 - PROD readonly role is write-proof"
psql_prod -q <<SQL >/dev/null
CREATE ROLE readonly LOGIN PASSWORD '${READONLY_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE account TO readonly;
GRANT USAGE ON SCHEMA public TO readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly;
SQL

ro_select="$(docker exec -i "${PROD_CONTAINER}" psql -tAX -U readonly -d account \
  -c 'SELECT count(*) FROM users' 2>&1 | tr -d '[:space:]')"
check "readonly can SELECT users" "3" "${ro_select}"

for stmt in \
  "INSERT INTO users (username,password) VALUES ('evil','x')" \
  "UPDATE users SET username='evil'" \
  "DELETE FROM users"; do
  out="$(docker exec -i "${PROD_CONTAINER}" psql -tAX -U readonly -d account -c "${stmt}" 2>&1 || true)"
  if grep -q "permission denied" <<<"${out}"; then
    ok "readonly blocked: ${stmt%% *} -> permission denied"
  else
    bad "readonly NOT blocked for '${stmt}': ${out}"
  fi
done

SOURCE_DSN="postgres://readonly:${READONLY_PASSWORD}@127.0.0.1:${PROD_PORT}/account?sslmode=disable"
TARGET_DSN="postgres://postgres:postgres@127.0.0.1:${UAT_PORT}/account?sslmode=disable"

# ------------------------------------------------------------------------------
# 4. Guide 4.1 - export
# ------------------------------------------------------------------------------
log "4. V2 - guide 4.1: migratectl export over the readonly DSN"
SNAPSHOT="${WORKDIR}/account-prod-snapshot.yaml"
"${MIGRATECTL_BIN}" export --dsn "${SOURCE_DSN}" --output "${SNAPSHOT}" >"${WORKDIR}/export.log" 2>&1
sed 's/^/    /' "${WORKDIR}/export.log"
check "snapshot version is v1" "v1" "$(awk '/^  version:/{print $2; exit}' "${SNAPSHOT}")"
check "export reports 3 users" "1" "$(grep -c 'Exported 3 users' "${WORKDIR}/export.log")"
check "snapshot carries all 3 PROD users" "3" \
  "$(grep -cE '^\s+username: prod-' "${SNAPSHOT}")"
check "snapshot file mode is 0600" "600" "$(stat -f '%OLp' "${SNAPSHOT}" 2>/dev/null || stat -c '%a' "${SNAPSHOT}")"
if grep -q 'schemaHash:' "${SNAPSHOT}"; then ok "snapshot carries schemaHash"; else bad "snapshot missing schemaHash"; fi

# ------------------------------------------------------------------------------
# 5. Guide 4.2 - dry run must not write
# ------------------------------------------------------------------------------
log "5. V3 - guide 4.2: --dry-run leaves UAT untouched"
before_users="$(uat_count users)"; before_ids="$(uat_count identities)"; before_sess="$(uat_count sessions)"
"${MIGRATECTL_BIN}" import --dsn "${TARGET_DSN}" --file "${SNAPSHOT}" \
  --regenerate-user-uuids --dry-run --merge --merge-strategy timestamp >"${WORKDIR}/dryrun.log" 2>&1
sed 's/^/    /' "${WORKDIR}/dryrun.log"
check "dry-run reports 2 new users" "1" "$(grep -c 'users inserted=2' "${WORKDIR}/dryrun.log")"
check "UAT users unchanged after dry-run"      "${before_users}" "$(uat_count users)"
check "UAT identities unchanged after dry-run" "${before_ids}"   "$(uat_count identities)"
check "UAT sessions unchanged after dry-run"   "${before_sess}"  "$(uat_count sessions)"

# ------------------------------------------------------------------------------
# 6. Guide 4.3 - real merge
# ------------------------------------------------------------------------------
log "6. V4 - guide 4.3: --merge --merge-strategy timestamp applies"
check "legacy overlay device seeded" "1" "$(psql_uat -tAc "SELECT count(*) FROM overlay_devices WHERE user_uuid='11111111-1111-4111-8111-111111111111'" | tr -d '[:space:]')"
check "legacy subscription seeded" "1" "$(psql_uat -tAc "SELECT count(*) FROM subscriptions WHERE user_uuid='11111111-1111-4111-8111-111111111111'" | tr -d '[:space:]')"
set +e
"${MIGRATECTL_BIN}" import --dsn "${TARGET_DSN}" --file "${SNAPSHOT}" \
  --regenerate-user-uuids --merge --merge-strategy timestamp >"${WORKDIR}/apply.log" 2>&1
apply_rc=$?
set -e
if [ "${apply_rc}" -ne 0 ]; then
  sed 's/^/    /' "${WORKDIR}/apply.log" >&2
  exit "${apply_rc}"
fi
sed 's/^/    /' "${WORKDIR}/apply.log"
check "UAT users after merge (3 prod + 1 uat-only)" "4" "$(uat_count users)"
check "UAT identities after merge" "2" "$(uat_count identities)"
check "UAT sessions after merge"   "1" "$(uat_count sessions)"
check "V7 UAT-only user survived" "uat-only-tester" \
  "$(psql_uat -tAc "SELECT username FROM users WHERE uuid='99999999-9999-4999-8999-999999999999'" | tr -d '[:space:]')"

alice_uuid="$(psql_uat -tAc "SELECT uuid FROM users WHERE email='alice@svc.plus'" | tr -d '[:space:]')"
bob_uuid="$(psql_uat -tAc "SELECT uuid FROM users WHERE email='bob@svc.plus'" | tr -d '[:space:]')"
check "alice proxy UUID preserved" "aaaa1111-1111-4111-8111-111111111111" \
  "$(psql_uat -tAc "SELECT proxy_uuid FROM users WHERE email='alice@svc.plus'" | tr -d '[:space:]')"
check "bob proxy UUID preserved" "bbbb2222-2222-4222-8222-222222222222" \
  "$(psql_uat -tAc "SELECT proxy_uuid FROM users WHERE email='bob@svc.plus'" | tr -d '[:space:]')"
check "alice identity UUID regenerated" "different" \
  "$([ "${alice_uuid}" != '11111111-1111-4111-8111-111111111111' ] && echo different || echo same)"
check "bob identity UUID regenerated" "different" \
  "$([ "${bob_uuid}" != '22222222-2222-4222-8222-222222222222' ] && echo different || echo same)"
check "alice identity follows regenerated UUID" "1" \
  "$(psql_uat -tAc "SELECT count(*) FROM identities WHERE user_uuid='${alice_uuid}' AND external_id='gh-alice'" | tr -d '[:space:]')"
check "alice session follows regenerated UUID" "1" \
  "$(psql_uat -tAc "SELECT count(*) FROM sessions WHERE user_uuid='${alice_uuid}' AND token='tok-alice'" | tr -d '[:space:]')"
check "alice overlay device follows regenerated UUID" "1" \
  "$(psql_uat -tAc "SELECT count(*) FROM overlay_devices WHERE user_uuid='${alice_uuid}' AND id='legacy-device'" | tr -d '[:space:]')"
check "alice overlay ack follows regenerated UUID" "1" \
  "$(psql_uat -tAc "SELECT count(*) FROM overlay_config_acks WHERE user_uuid='${alice_uuid}' AND device_id='legacy-device'" | tr -d '[:space:]')"
check "alice subscription follows regenerated UUID" "1" \
  "$(psql_uat -tAc "SELECT count(*) FROM subscriptions WHERE user_uuid='${alice_uuid}' AND external_id='legacy-subscription'" | tr -d '[:space:]')"

# ------------------------------------------------------------------------------
# 7. Idempotency
# ------------------------------------------------------------------------------
log "7. V5 - re-running the same import is idempotent"
"${MIGRATECTL_BIN}" import --dsn "${TARGET_DSN}" --file "${SNAPSHOT}" \
  --regenerate-user-uuids --merge --merge-strategy timestamp >"${WORKDIR}/apply2.log" 2>&1
sed 's/^/    /' "${WORKDIR}/apply2.log"
check "no new users on replay"      "4" "$(uat_count users)"
check "no new identities on replay" "2" "$(uat_count identities)"
check "no new sessions on replay"   "1" "$(uat_count sessions)"
check "second run inserts nothing"  "1" "$(grep -c 'users inserted=0' "${WORKDIR}/apply2.log")"

# ------------------------------------------------------------------------------
# 8. Timestamp conflict resolution
# ------------------------------------------------------------------------------
log "8. V6 - newer UAT row wins under --merge-strategy timestamp"
psql_uat -q -c "UPDATE users SET username='uat-edited-alice', updated_at = now() + interval '1 day' \
  WHERE email='alice@svc.plus'" >/dev/null
"${MIGRATECTL_BIN}" import --dsn "${TARGET_DSN}" --file "${SNAPSHOT}" \
  --regenerate-user-uuids --merge --merge-strategy timestamp >"${WORKDIR}/apply3.log" 2>&1
sed 's/^/    /' "${WORKDIR}/apply3.log"
check "newer UAT edit preserved" "uat-edited-alice" \
  "$(psql_uat -tAc "SELECT username FROM users WHERE email='alice@svc.plus'" | tr -d '[:space:]')"
if grep -qE 'Conflicts (resolved|skipped)=' "${WORKDIR}/apply3.log"; then
  ok "conflict accounted for in report"
else
  bad "no conflict line in report"
fi

# ------------------------------------------------------------------------------
# 9. The wrapper script end to end
# ------------------------------------------------------------------------------
log "9. accounts_data_migration.sh end-to-end against the local pair"
psql_uat -q -c "UPDATE users SET username='prod-alice', updated_at = now() - interval '30 day' \
  WHERE email='alice@svc.plus'" >/dev/null

rc=0
MIGRATION_SOURCE_DSN="${SOURCE_DSN}" MIGRATION_TARGET_DSN="${TARGET_DSN}" \
  MIGRATECTL_BIN="${MIGRATECTL_BIN}" SNAPSHOT_FILE="${WORKDIR}/wrapper-snapshot.yaml" \
  DRY_RUN="true" bash "${MIGRATION_SCRIPT}" >"${WORKDIR}/wrapper-dry.log" 2>&1 || rc=$?
check "wrapper DRY_RUN=true exit code" "0" "${rc}"
check "wrapper DRY_RUN=true wrote nothing" "4" "$(uat_count users)"

rc=0
MIGRATION_SOURCE_DSN="${SOURCE_DSN}" MIGRATION_TARGET_DSN="${TARGET_DSN}" \
  MIGRATECTL_BIN="${MIGRATECTL_BIN}" SNAPSHOT_FILE="${WORKDIR}/wrapper-snapshot.yaml" \
  DRY_RUN="false" bash "${MIGRATION_SCRIPT}" >"${WORKDIR}/wrapper-apply.log" 2>&1 || rc=$?
check "wrapper apply exit code" "0" "${rc}"
check "wrapper ran post-import convergence verification" "1" \
  "$(grep -c 'Verification passed' "${WORKDIR}/wrapper-apply.log")"
check "wrapper redacts DSN passwords in its log" "0" \
  "$(grep -c "${READONLY_PASSWORD}" "${WORKDIR}/wrapper-apply.log" || true)"
if [ -e "${WORKDIR}/wrapper-snapshot.yaml" ]; then
  bad "snapshot left on disk after wrapper run (PII cleanup missing)"
else
  ok "wrapper removed the snapshot after the run"
fi

log "10. Snapshot is wiped even when the import fails mid-run"
rc=0
MIGRATION_SOURCE_DSN="${SOURCE_DSN}" \
  MIGRATION_TARGET_DSN="postgres://postgres:postgres@127.0.0.1:1/account?sslmode=disable&host=localhost" \
  MIGRATECTL_BIN="${MIGRATECTL_BIN}" SNAPSHOT_FILE="${WORKDIR}/failed-snapshot.yaml" \
  DRY_RUN="false" bash "${MIGRATION_SCRIPT}" >"${WORKDIR}/wrapper-fail.log" 2>&1 || rc=$?
if [ "${rc}" -ne 0 ]; then ok "wrapper fails loudly on an unreachable target (rc=${rc})"; else bad "wrapper exited 0 despite an unreachable target"; fi
if [ -e "${WORKDIR}/failed-snapshot.yaml" ]; then
  bad "snapshot with password hashes survived a failed run"
else
  ok "snapshot wiped by the EXIT trap after failure"
fi

# ------------------------------------------------------------------------------
log "Result"
printf 'E2E verification: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
