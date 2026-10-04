#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Endpoint preflight test for accounts_data_migration_ssh.sh
#
# Run 37173103865 burned a 60-attempt readiness loop on a PROD source name that
# had no DNS record, then reported a generic "SSH did not become ready". The
# preflight now classifies DNS / EDGE / CONNECT / AUTH / HOSTKEY failures with
# distinct exit codes, and only the boot-window class (CONNECT) is retried.
#
# getent and ssh/scp are stubbed on PATH, so no case touches live DNS or a host.
# Every failing case must stop before anything is staged (no mkdir/scp/docker)
# and must not run the snapshot cleanup against a host that was never staged.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SCRIPT="${SCRIPT_DIR}/../../../scripts/data_operations/accounts_data_migration_ssh.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

# getent ahosts <name>: answers from GETENT_MAP ("<name> <addr> [addr...]"),
# exit 2 (not found) otherwise -- the same contract as glibc getent.
cat >"${WORKDIR}/getent" <<'STUB'
#!/usr/bin/env bash
echo "getent $*" >>"${STUB_LOG}"
name="$2"
line="$(awk -v n="${name}" '$1 == n' "${GETENT_MAP}")"
[ -n "${line}" ] || exit 2
for addr in ${line#* }; do
  printf '%s STREAM %s\n%s DGRAM\n' "${addr}" "${name}" "${addr}"
done
STUB

# ssh: the readiness probe (remote command `true`) follows SSH_PLAN
# ("<address> <mode>"); every other command succeeds. Modes:
#   ok | refused | denied | hostkey | refused:<n> (refused n times, then ok)
cat >"${WORKDIR}/ssh" <<'STUB'
#!/usr/bin/env bash
echo "ssh $*" >>"${STUB_LOG}"
last="${!#}"
target=""
for arg in "$@"; do
  case "${arg}" in *@*) target="${arg#*@}" ;; esac
done
[ "${last}" = "true" ] || exit 0
mode="$(awk -v a="${target}" '$1 == a {print $2}' "${SSH_PLAN}")"
case "${mode:-ok}" in
  ok) exit 0 ;;
  refused)
    echo "ssh: connect to host ${target} port 22: Connection refused" >&2; exit 255 ;;
  denied)
    echo "root@${target}: Permission denied (publickey)." >&2; exit 255 ;;
  hostkey)
    echo "Host key verification failed." >&2; exit 255 ;;
  refused:*)
    counter="${STUB_LOG}.${target}"
    n="$(cat "${counter}" 2>/dev/null || echo 0)"
    echo $((n + 1)) >"${counter}"
    if [ "${n}" -lt "${mode#refused:}" ]; then
      echo "ssh: connect to host ${target} port 22: Connection refused" >&2; exit 255
    fi
    exit 0 ;;
esac
STUB

cat >"${WORKDIR}/scp" <<'STUB'
#!/usr/bin/env bash
echo "scp $*" >>"${STUB_LOG}"
STUB

printf '#!/bin/sh\nexit 0\n' >"${WORKDIR}/migratectl"
chmod +x "${WORKDIR}/getent" "${WORKDIR}/ssh" "${WORKDIR}/scp" "${WORKDIR}/migratectl"

SRC="console-selfhost-prod.svc.plus"
TGT="console-selfhost-uat.onwalk.net"
PASS=0
FAIL=0

# run_case <getent-map> <ssh-plan> [cmdb-json]; sets RC, OUT, LOG
run_case() {
  printf '%b' "$1" >"${WORKDIR}/getent.map"
  printf '%b' "$2" >"${WORKDIR}/ssh.plan"
  rm -f "${WORKDIR}"/stub.log*
  : >"${WORKDIR}/stub.log"
  local cmdb=""
  if [ -n "${3:-}" ]; then
    printf '%s' "$3" >"${WORKDIR}/cmdb.json"
    cmdb="${WORKDIR}/cmdb.json"
  fi
  RC=0
  PATH="${WORKDIR}:${PATH}" STUB_LOG="${WORKDIR}/stub.log" \
  GETENT_MAP="${WORKDIR}/getent.map" SSH_PLAN="${WORKDIR}/ssh.plan" \
  MIGRATION_SOURCE_HOST="${SRC}" MIGRATION_TARGET_HOST="${TGT}" \
  MIGRATECTL_BIN="${WORKDIR}/migratectl" CMDB_FILE="${cmdb}" \
  SSH_READY_ATTEMPTS=3 SSH_READY_INTERVAL_SECONDS=0 SSH_AUTH_ATTEMPTS=2 \
  DRY_RUN=true \
    bash "${TARGET_SCRIPT}" >"${WORKDIR}/out.log" 2>&1 || RC=$?
  OUT="$(cat "${WORKDIR}/out.log")"
  LOG="$(cat "${WORKDIR}/stub.log")"
}

staged() { grep -qE "mkdir -p|docker run|^scp " <<<"${LOG}"; }
probes() { grep -c "ssh .*@$1 true$" <<<"${LOG}" || true; }

check() { # <name> <condition...>
  local name="$1"; shift
  if "$@"; then
    PASS=$((PASS + 1)); printf '  [PASS] %s\n' "${name}"
  else
    FAIL=$((FAIL + 1)); printf '  [FAIL] %s (rc=%s)\n' "${name}" "${RC}"
    sed 's/^/         /' <<<"${OUT}"
  fi
}

failed_before_staging() { # <want-rc> <class> <role>
  [ "${RC}" = "$1" ] && ! staged &&
    grep -Fq "[PREFLIGHT:$2] $3:" <<<"${OUT}" &&
    grep -Fq "::error title=Accounts migration preflight ($2)::" <<<"${OUT}" &&
    ! grep -q "rm -rf" <<<"${LOG}"
}

echo "=== SSH endpoint preflight classification ==="

run_case "${TGT} 192.0.2.20\n" ""
check "source without A/AAAA fails as DNS (exit 10) on the first look" \
  failed_before_staging 10 DNS source
check "DNS failure never opens an SSH session to either host" \
  test "$(grep -c '^ssh ' <<<"${LOG}" || true)" = 0
check "DNS failure names the missing record" \
  grep -Fq "${SRC} has no A/AAAA record and no CMDB entry" <<<"${OUT}"

run_case "${SRC} 192.0.2.10\n" ""
check "target without A/AAAA fails as DNS (exit 10)" \
  failed_before_staging 10 DNS target

run_case "${SRC} 172.67.169.59 104.21.79.67\n${TGT} 192.0.2.20\n" ""
check "source on a Cloudflare IPv4 edge fails as EDGE (exit 11)" \
  failed_before_staging 11 EDGE source
check "EDGE failure lists the edge addresses" \
  grep -Fq "172.67.169.59" <<<"${OUT}"

run_case "${SRC} 2606:4700:3035::6815:4f43\n${TGT} 192.0.2.20\n" ""
check "source on a Cloudflare IPv6 edge fails as EDGE (exit 11)" \
  failed_before_staging 11 EDGE source

run_case "${SRC} 192.0.2.10\n${TGT} 192.0.2.20\n" "${SRC} refused\n"
check "closed TCP/22 fails as CONNECT (exit 12) after the boot window" \
  failed_before_staging 12 CONNECT source
check "CONNECT is retried SSH_READY_ATTEMPTS times" \
  test "$(probes "${SRC}")" = 3

run_case "${SRC} 192.0.2.10\n${TGT} 192.0.2.20\n" "${SRC} refused:2\n"
check "a host that finishes booting inside the window proceeds" \
  bash -c "[ ${RC} = 0 ]"
check "the booted host is then staged" staged

run_case "${SRC} 192.0.2.10\n${TGT} 192.0.2.20\n" "${SRC} denied\n"
check "rejected deploy key fails as AUTH (exit 13)" \
  failed_before_staging 13 AUTH source
check "AUTH stops after SSH_AUTH_ATTEMPTS, not the whole boot window" \
  test "$(probes "${SRC}")" = 2

run_case "${SRC} 192.0.2.10\n${TGT} 192.0.2.20\n" "${SRC} hostkey\n"
check "host key mismatch fails as HOSTKEY (exit 14) without retry" \
  failed_before_staging 14 HOSTKEY source
check "HOSTKEY is not retried" \
  test "$(probes "${SRC}")" = 1

run_case "${TGT} 192.0.2.20\n" "" "{\"${SRC}\": {\"ip\": \"198.51.100.7\"}}"
check "a CMDB address is used as-is and never looked up in DNS" \
  bash -c "[ ${RC} = 0 ] && ! grep -q 'getent ahosts ${SRC}' <<<'${LOG}'"
check "the CMDB address is the one probed and staged" \
  bash -c "grep -q 'ssh .*@198.51.100.7 true' <<<'${LOG}' && grep -q 'ssh .*@198.51.100.7 mkdir -p' <<<'${LOG}'"

run_case "${SRC} 192.0.2.10\n${TGT} 192.0.2.20\n" ""
check "two healthy endpoints proceed to staging" \
  bash -c "[ ${RC} = 0 ]"
check "a staged run cleans up the hosts it staged" \
  grep -q "rm -rf /root/.accounts-migration" <<<"${LOG}"

echo
printf 'SSH preflight tests: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
