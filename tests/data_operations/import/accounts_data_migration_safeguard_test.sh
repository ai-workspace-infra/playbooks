#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Safeguard unit test for accounts_data_migration.sh (Layer 3 防呆断路熔断)
#
# Runs WITHOUT any database: migratectl is replaced by a stub that records the
# invocation, so the only thing under test is the DSN assertion logic.
#
# Usage: bash tests/data_operations/import/accounts_data_migration_safeguard_test.sh
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SCRIPT="${SCRIPT_DIR}/../../../scripts/data_operations/accounts_data_migration.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

# migratectl stub: records that a real migration command was reached.
cat >"${WORKDIR}/migratectl" <<'STUB'
#!/usr/bin/env bash
echo "$@" >>"${STUB_LOG}"
case "$1" in
  export)
    out=""
    while [ "$#" -gt 0 ]; do
      [ "$1" = "--output" ] && out="$2"
      shift
    done
    [ -n "${out}" ] && printf 'metadata:\n  version: v1\nusers: []\n' >"${out}"
    ;;
esac
exit 0
STUB
chmod +x "${WORKDIR}/migratectl"

PASS=0
FAIL=0

# assert_case <name> <expected_exit> <expect_migratectl_ran:yes|no> <target_dsn> [source_dsn]
assert_case() {
  local name="$1" want_rc="$2" want_ran="$3" target="$4"
  local source="${5-postgres://readonly:pw@console.svc.plus:5432/account?sslmode=require}"
  local log="${WORKDIR}/stub.log"
  : >"${log}"

  local rc=0
  STUB_LOG="${log}" \
  MIGRATION_SOURCE_DSN="${source}" \
  MIGRATION_TARGET_DSN="${target}" \
  MIGRATECTL_BIN="${WORKDIR}/migratectl" \
  SNAPSHOT_FILE="${WORKDIR}/snapshot.yaml" \
  DRY_RUN="true" \
    bash "${TARGET_SCRIPT}" >"${WORKDIR}/out.log" 2>&1 || rc=$?

  local ran="no"
  [ -s "${log}" ] && ran="yes"

  if [ "${rc}" = "${want_rc}" ] && [ "${ran}" = "${want_ran}" ]; then
    PASS=$((PASS + 1))
    printf '  [PASS] %s (rc=%s migratectl_ran=%s)\n' "${name}" "${rc}" "${ran}"
  else
    FAIL=$((FAIL + 1))
    printf '  [FAIL] %s: want rc=%s ran=%s, got rc=%s ran=%s\n' \
      "${name}" "${want_rc}" "${want_ran}" "${rc}" "${ran}"
    sed 's/^/         /' "${WORKDIR}/out.log"
  fi
}

echo "=== Layer 3 safeguard assertions ==="

# --- Must ABORT before touching any database ---------------------------------
assert_case "reject PROD target (console.svc.plus)" 1 no \
  "postgres://account_user:pw@console.svc.plus:5432/account"
assert_case "reject PROD target (bare svc.plus)" 1 no \
  "postgres://account_user:pw@svc.plus:5432/account"
assert_case "reject PROD target disguised with UAT host in query" 1 no \
  "postgres://account_user:pw@console.svc.plus:5432/account?application_name=onwalk.net"
assert_case "reject unknown domain target" 1 no \
  "postgres://account_user:pw@db.example.com:5432/account"
assert_case "reject empty target DSN" 1 no ""
assert_case "reject empty source DSN" 1 no \
  "postgres://account_user:pw@agent-proxy.onwalk.net:5432/account" ""
assert_case "reject identical source and target DSN" 1 no \
  "postgres://account_user:pw@agent-proxy.onwalk.net:5432/account" \
  "postgres://account_user:pw@agent-proxy.onwalk.net:5432/account"

# --- Must PROCEED (recognised UAT / local targets) ---------------------------
assert_case "accept UAT target (agent-proxy.onwalk.net)" 0 yes \
  "postgres://account_user:pw@agent-proxy.onwalk.net:5432/account"
assert_case "accept UAT target (console-uat.onwalk.net)" 0 yes \
  "postgres://account_user:pw@console-uat.onwalk.net:5432/account"
assert_case "accept local test target (127.0.0.1)" 0 yes \
  "postgres://account_user:pw@127.0.0.1:55434/account?sslmode=disable"

echo
printf 'Safeguard tests: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
