#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Safeguard unit test for accounts_data_migration_ssh.sh
#
# The SSH transport always connects over loopback inside the container netns, so
# the DSN can no longer tell PROD from UAT. Host identity is the only remaining
# anchor -- which makes these assertions the whole of safeguard layer 3 for this
# transport, and worth testing on their own.
#
# Runs without SSH or a database: `ssh`/`scp` are stubbed on PATH, so a case that
# reaches them is recorded as "would have contacted a host".
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SCRIPT="${SCRIPT_DIR}/../../../scripts/data_operations/accounts_data_migration_ssh.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

for stub in ssh scp; do
  cat >"${WORKDIR}/${stub}" <<'STUB'
#!/usr/bin/env bash
echo "$0 $*" >>"${STUB_LOG}"
exit 0
STUB
  chmod +x "${WORKDIR}/${stub}"
done

# The endpoint preflight resolves names with getent. Stub it with a TEST-NET
# address so these host-identity cases never depend on live DNS (several names
# below have no record, and console.svc.plus is CDN-proxied).
cat >"${WORKDIR}/getent" <<'STUB'
#!/usr/bin/env bash
echo "192.0.2.10      STREAM $2"
STUB
chmod +x "${WORKDIR}/getent"

# A stand-in for the migratectl binary; only its executable bit is inspected
# before the safeguards run.
printf '#!/bin/sh\nexit 0\n' >"${WORKDIR}/migratectl"
chmod +x "${WORKDIR}/migratectl"

PASS=0
FAIL=0

# assert_case <name> <want_rc> <want_contacted_host:yes|no> <source_host> <target_host> [source_db_user]
assert_case() {
  local name="$1" want_rc="$2" want_contact="$3" src="$4" tgt="$5" dbuser="${6:-readonly}"
  local log="${WORKDIR}/stub.log"
  : >"${log}"

  local rc=0
  PATH="${WORKDIR}:${PATH}" STUB_LOG="${log}" \
  MIGRATION_SOURCE_HOST="${src}" \
  MIGRATION_TARGET_HOST="${tgt}" \
  MIGRATION_SOURCE_DB_USER="${dbuser}" \
  MIGRATECTL_BIN="${WORKDIR}/migratectl" \
  DRY_RUN="true" \
    bash "${TARGET_SCRIPT}" >"${WORKDIR}/out.log" 2>&1 || rc=$?

  # The EXIT trap always calls ssh to clean up, so only staging/export counts as
  # "contacted a host" -- that is the line a safeguard must stop the run before.
  local contacted="no"
  grep -qE "mkdir -p|docker run|scp " "${log}" 2>/dev/null && contacted="yes"

  if [ "${rc}" = "${want_rc}" ] && [ "${contacted}" = "${want_contact}" ]; then
    PASS=$((PASS + 1))
    printf '  [PASS] %s (rc=%s contacted=%s)\n' "${name}" "${rc}" "${contacted}"
  else
    FAIL=$((FAIL + 1))
    printf '  [FAIL] %s: want rc=%s contacted=%s, got rc=%s contacted=%s\n' \
      "${name}" "${want_rc}" "${want_contact}" "${rc}" "${contacted}"
    sed 's/^/         /' "${WORKDIR}/out.log"
  fi
}

echo "=== SSH transport safeguard assertions (host-anchored) ==="

# --- Must ABORT before contacting any host -----------------------------------
assert_case "reject PROD target host" 1 no \
  "console.svc.plus" "console.svc.plus"
assert_case "reject PROD target even with valid PROD source" 1 no \
  "console.svc.plus" "postgresql-saas.svc.plus"
assert_case "reject unknown target domain" 1 no \
  "console.svc.plus" "db.example.com"
assert_case "reject canonical UAT target alias" 1 no \
  "console.svc.plus" "console-uat.onwalk.net"
assert_case "reject non-PROD source (UAT -> UAT)" 1 no \
  "console-uat.onwalk.net" "console-selfhost-uat.onwalk.net"
assert_case "reject empty target host" 1 no \
  "console.svc.plus" ""
assert_case "reject empty source host" 1 no \
  "" "console-selfhost-uat.onwalk.net"
assert_case "reject write-capable source DB role" 1 no \
  "console.svc.plus" "console-selfhost-uat.onwalk.net" "postgres"
assert_case "reject account_user as source DB role" 1 no \
  "console.svc.plus" "console-selfhost-uat.onwalk.net" "account_user"

# --- Must PROCEED past the safeguards ----------------------------------------
assert_case "accept PROD -> UAT" 0 yes \
  "console.svc.plus" "console-selfhost-uat.onwalk.net"

echo
printf 'SSH safeguard tests: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
