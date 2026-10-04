#!/bin/bash
set -euo pipefail

# ==============================================================================
# Accounts Data Migration over SSH (PROD -> UAT)  --  transport fallback
#
# Neither database is reachable as a PostgreSQL endpoint from a runner: the only
# public ports (PROD 5433 / UAT 15433) are stunnel TLS listeners, and a libpq
# client cannot speak to those under any sslmode -- it never reaches
# authentication. This transport sidesteps that by running migratectl on each
# host instead of pulling the wire to the runner.
#
# migratectl runs inside the PostgreSQL container's network namespace, where
# pg_hba grants `trust` on 127.0.0.1. `trust` waives password verification only;
# it does not grant privileges, so the SELECT-only `readonly` role on the source
# is still enforced by the database (verified: INSERT/UPDATE both return
# `permission denied for table users`).
#
# ⚠️ TRADE-OFF, ACCEPTED DELIBERATELY: this transport requires root SSH on the
# PROD host, which is strictly more privilege than the read-only database
# password the direct transport needs. Anyone holding it can `docker exec` as
# superuser, so safeguard layer 4 no longer constrains the *pipeline* (it still
# constrains human operators). The direct/stunnel transport keeps layer 4 real.
# Because the DSN is now always loopback, the environment assertions are
# anchored on the SSH host names instead -- that is the only thing left in this
# transport that distinguishes PROD from UAT.
#
# Env:
#   MIGRATION_SOURCE_HOST       PROD host (required, asserted PROD)
#   MIGRATION_TARGET_HOST       UAT host  (required, asserted UAT)
#   MIGRATION_SOURCE_CONTAINER  PROD PostgreSQL container name
#   MIGRATION_TARGET_CONTAINER  UAT PostgreSQL container name
#   MIGRATION_SOURCE_DB_USER    PROD role (default readonly, asserted read-only)
#   MIGRATION_TARGET_DB_USER    UAT role  (default account_user)
#   MIGRATION_DB                database name (default account)
#   MIGRATECTL_BIN              linux/amd64 migratectl built on the runner
#   SSH_USER                    default root
#   DRY_RUN                     "true" = stop after the preview (default true).
#                               NOT a simulation: the snapshot is still exported
#                               from PROD and copied to UAT for the preview, so
#                               a dry run moves the same password hashes, MFA
#                               secrets and session tokens as a real run.
#   MIGRATION_EMAIL_FILTER      optional --email keyword, to rehearse on one
#                               account instead of moving every production user
#   SKIP_VERIFY                 "true" = skip post-import convergence check
#   SSH_READY_ATTEMPTS          TCP/22 attempts while a host boots (default 60)
#   SSH_AUTH_ATTEMPTS           key-rejected attempts before failing (default 5)
#
# Exit codes: 1 safeguard/runtime failure; 10-14 endpoint preflight failure
# (DNS, EDGE, CONNECT, AUTH, HOSTKEY -- see "Endpoint preflight" below).
# ==============================================================================

SOURCE_HOST="${MIGRATION_SOURCE_HOST:-}"
TARGET_HOST="${MIGRATION_TARGET_HOST:-}"
SOURCE_CONTAINER="${MIGRATION_SOURCE_CONTAINER:-postgresql-svc-plus}"
TARGET_CONTAINER="${MIGRATION_TARGET_CONTAINER:-web-saas-postgresql}"
SOURCE_DB_USER="${MIGRATION_SOURCE_DB_USER:-readonly}"
TARGET_DB_USER="${MIGRATION_TARGET_DB_USER:-account_user}"
DB_NAME="${MIGRATION_DB:-account}"
MIGRATECTL_BIN="${MIGRATECTL_BIN:-}"
SSH_USER="${SSH_USER:-root}"
SSH_KEY_PATH="${MIGRATION_SSH_KEY_PATH:-${HOME}/.ssh/id_deploy}"
DRY_RUN="${DRY_RUN:-true}"
SKIP_VERIFY="${SKIP_VERIFY:-false}"
EMAIL_FILTER="${MIGRATION_EMAIL_FILTER:-}"
RUNTIME_IMAGE="${MIGRATION_RUNTIME_IMAGE:-alpine:latest}"
SSH_READY_ATTEMPTS="${SSH_READY_ATTEMPTS:-60}"
SSH_READY_INTERVAL_SECONDS="${SSH_READY_INTERVAL_SECONDS:-3}"
SSH_READY_CONNECT_TIMEOUT_SECONDS="${SSH_READY_CONNECT_TIMEOUT_SECONDS:-5}"
SSH_AUTH_ATTEMPTS="${SSH_AUTH_ATTEMPTS:-5}"

REMOTE_DIR="/root/.accounts-migration.$$"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -i "${SSH_KEY_PATH}")
SSH_READY_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o "ConnectTimeout=${SSH_READY_CONNECT_TIMEOUT_SECONDS}" -i "${SSH_KEY_PATH}")

# The snapshot holds password hashes and session tokens. It must not outlive the
# run on either host, including on every failure path.
# Only hosts that reached staging can hold ${REMOTE_DIR}, so only those are
# cleaned. Each address is recorded before its mkdir, which keeps a host whose
# staging failed halfway on the list. Cleaning every configured host instead
# meant a preflight failure on an unreachable host still waited out a full SSH
# ConnectTimeout just to delete nothing (run 37173103865: 20s).
STAGED_ADDRS=()
cleanup() {
  local rc=$?
  local h
  for h in ${STAGED_ADDRS[@]+"${STAGED_ADDRS[@]}"}; do
    ssh "${SSH_OPTS[@]}" "${SSH_USER}@${h}" "rm -rf ${REMOTE_DIR}" >/dev/null 2>&1 || true
  done
  if [ "${#STAGED_ADDRS[@]}" -gt 0 ]; then
    echo "[CLEANUP] Removed ${REMOTE_DIR} from ${STAGED_ADDRS[*]}."
  else
    echo "[CLEANUP] Nothing was staged on any host."
  fi
  exit "${rc}"
}
trap cleanup EXIT

if [ -z "${SOURCE_HOST}" ] || [ -z "${TARGET_HOST}" ]; then
  echo "ERROR: MIGRATION_SOURCE_HOST and MIGRATION_TARGET_HOST are required." >&2
  exit 1
fi
if [ -z "${MIGRATECTL_BIN}" ] || [ ! -x "${MIGRATECTL_BIN}" ]; then
  echo "ERROR: MIGRATECTL_BIN must point at an executable linux/amd64 migratectl." >&2
  exit 1
fi
for tool in getent python3; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "ERROR: ${tool} is required by the endpoint preflight." >&2
    exit 1
  }
done

echo "=========================================="
echo " Accounts Migration over SSH (PROD -> UAT) "
echo "=========================================="
echo "  source : ${SSH_USER}@${SOURCE_HOST} (${SOURCE_CONTAINER}, role ${SOURCE_DB_USER})"
echo "  target : ${SSH_USER}@${TARGET_HOST} (${TARGET_CONTAINER}, role ${TARGET_DB_USER})"
echo "  dry-run: ${DRY_RUN}"
[ -n "${EMAIL_FILTER}" ] && echo "  filter : --email ${EMAIL_FILTER}"

# ------------------------------------------------------------------------------
# Safeguard assertions -- anchored on host identity, not on a DSN string
# ------------------------------------------------------------------------------
echo "[SAFEGUARD-CHECK] Validating migration direction..."

if [[ "${TARGET_HOST}" =~ "svc.plus" ]]; then
  echo "[CRITICAL ERROR] Target host is a PROD host (svc.plus)! Aborting to prevent writing to PROD." >&2
  exit 1
fi
if [[ ! "${TARGET_HOST}" =~ "onwalk.net" ]]; then
  echo "[CRITICAL ERROR] Target host is not a recognised UAT host (expected *.onwalk.net). Aborting." >&2
  exit 1
fi
if [[ "${TARGET_HOST}" == "console-uat.onwalk.net" ||
      "${TARGET_HOST}" == "accounts-uat.onwalk.net" ||
      "${TARGET_HOST}" == "billing-uat.onwalk.net" ||
      "${TARGET_HOST}" == "postgresql-uat.onwalk.net" ||
      "${TARGET_HOST}" == "agent-proxy-uat.onwalk.net" ]]; then
  echo "[CRITICAL ERROR] Target host is a canonical UAT alias, not a selfhost SSH endpoint: ${TARGET_HOST}. Aborting." >&2
  exit 1
fi
if [[ ! "${TARGET_HOST}" =~ -selfhost-uat\.onwalk\.net$ ]]; then
  echo "[CRITICAL ERROR] Target host is not a GitOps selfhost UAT endpoint: ${TARGET_HOST}. Aborting." >&2
  exit 1
fi
if [[ ! "${SOURCE_HOST}" =~ "svc.plus" ]]; then
  echo "[CRITICAL ERROR] Source host is not a PROD host (expected *.svc.plus). Aborting." >&2
  exit 1
fi
if [ "${SOURCE_HOST}" = "${TARGET_HOST}" ]; then
  echo "[CRITICAL ERROR] Source and target host are identical! Aborting." >&2
  exit 1
fi
# The source role is the last thing standing between this pipeline and a write
# to PROD, so it is asserted rather than assumed.
if [ "${SOURCE_DB_USER}" != "readonly" ]; then
  echo "[CRITICAL ERROR] Source DB role is '${SOURCE_DB_USER}', expected the SELECT-only 'readonly'." >&2
  exit 1
fi

echo "[SAFEGUARD-CHECK] Direction verified: PROD source -> UAT target."

# ------------------------------------------------------------------------------
# Connection addressing -- resolve through CMDB, never through DNS
#
# The safeguard block above deliberately keeps working on the *logical* host
# names: the whole direction check is "does this name say svc.plus / onwalk.net",
# so it must run before any name is replaced by an address.
#
# Connecting by those same names is what broke run 31348437225: this job and
# `update_uat_dns` have no dependency between them and started in the same
# second, so the canonical UAT alias still pointed at the previous host when
# STEP 0 opened its SSH session. Staging and the snapshot transfer succeeded
# anyway (mkdir, scp and cat exist everywhere), and the run only fell over at
# STEP 3 with `docker: command not found` -- ten seconds before DNS was
# repointed at the real web-saas host.
#
# Every other host-facing step in this pipeline already takes its address from
# cmdb/cmdb.json (also used by setup-deployment-runner); this script was the one
# exception. Reading the same source makes it immune to DNS state, so job
# ordering can no longer produce a wrong-host connection.
#
# CMDB_FILE is optional: a manual workflow_dispatch run has no CMDB artifact,
# and falling back to the name preserves that path unchanged.
resolve_host_address() { # <logical-host>
  local name="$1"
  if [ -z "${CMDB_FILE:-}" ] || [ ! -f "${CMDB_FILE}" ]; then
    echo "${name}"
    return
  fi
  local ip
  ip="$(jq -r --arg h "${name}" '.[$h].ip // empty' "${CMDB_FILE}" 2>/dev/null)"
  if [ -z "${ip}" ] || [ "${ip}" = "null" ]; then
    echo "${name}"
    return
  fi
  echo "${ip}"
}

SOURCE_ADDR="$(resolve_host_address "${SOURCE_HOST}")"
TARGET_ADDR="$(resolve_host_address "${TARGET_HOST}")"

for pair in "source:${SOURCE_HOST}:${SOURCE_ADDR}" "target:${TARGET_HOST}:${TARGET_ADDR}"; do
  role="${pair%%:*}"; rest="${pair#*:}"
  name="${rest%%:*}"; addr="${rest#*:}"
  if [ "${name}" = "${addr}" ]; then
    echo "[ADDRESS] ${role} ${name} -> resolving via DNS (no CMDB entry)"
  else
    echo "[ADDRESS] ${role} ${name} -> ${addr} (from CMDB)"
  fi
done

# ------------------------------------------------------------------------------
# Endpoint preflight -- prove each SSH endpoint is real before touching data
#
# Run 37173103865 spent three minutes in a 60-attempt readiness loop against
# console-selfhost-prod.svc.plus, a name with no A/AAAA record at all, and then
# reported only "SSH did not become ready". A missing record, a CDN-proxied
# name, a closed port and a rejected key all looked identical. Waiting fixes
# exactly one of those -- a host that is still booting -- so each failure is
# classified and only that one is retried:
#
#   DNS      exit 10  the name has no A/AAAA record           fail immediately
#   EDGE     exit 11  the name resolves to a CDN edge (proxy) fail immediately
#   CONNECT  exit 12  TCP/22 refused, timed out, or reset     retried (boot window)
#   AUTH     exit 13  the deploy key is rejected              brief retry, then fail
#   HOSTKEY  exit 14  the host key does not match known_hosts fail immediately
#
# None of these is fixed by substituting an address. The authoritative address
# comes from this run's CMDB or from a DNS record GitOps declares; anything
# else is a guess, and a guessed PROD address is not a safe input to a script
# that exports password hashes from it.

# Cloudflare's published edge ranges (https://www.cloudflare.com/ips/). A
# proxied record answers HTTP(S) only, so SSH to it can never succeed --
# console.svc.plus is such a name.
CDN_EDGE_CIDRS="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32"

is_ip_literal() { # <address>
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || "$1" == *:* ]]
}

# Prints every address in the given list that falls inside a CDN edge range.
cdn_edge_addresses() { # <address...>
  python3 - "${CDN_EDGE_CIDRS}" "$@" <<'PY_EDGE'
import ipaddress
import sys

networks = [ipaddress.ip_network(cidr) for cidr in sys.argv[1].split()]
for raw in sys.argv[2:]:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        continue
    if any(address.version == net.version and address in net for net in networks):
        print(raw)
PY_EDGE
}

classify_ssh_error() { # <ssh stderr>
  case "$1" in
    *"Could not resolve hostname"*|*"Name or service not known"*|*"Temporary failure in name resolution"*)
      echo DNS ;;
    *"Host key verification failed"*|*"REMOTE HOST IDENTIFICATION HAS CHANGED"*)
      echo HOSTKEY ;;
    *"Permission denied"*|*"Too many authentication failures"*)
      echo AUTH ;;
    *)
      echo CONNECT ;;
  esac
}

preflight_fail() { # <class> <exit-code> <role> <message>
  local class="$1" code="$2" role="$3" message="$4"
  echo "::error title=Accounts migration preflight (${class})::${role}: ${message}" >&2
  echo "[PREFLIGHT:${class}] ${role}: ${message}" >&2
  echo "[PREFLIGHT:${class}] No snapshot was exported; no host was modified." >&2
  exit "${code}"
}

preflight_endpoint() { # <role> <logical-host> <address>
  local role="$1" name="$2" address="$3"
  local resolved edge err class="" attempt auth_failures=0 last_err=""

  if is_ip_literal "${address}"; then
    resolved="${address}"
  else
    resolved="$(getent ahosts "${address}" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
    resolved="${resolved% }"
    if [ -z "${resolved}" ]; then
      preflight_fail DNS 10 "${role}" "${name} has no A/AAAA record and no CMDB entry. Publish the GitOps-declared record or supply this run's CMDB; do not substitute a guessed IP or a CDN-proxied alias."
    fi
  fi

  # shellcheck disable=SC2086 # one argument per resolved address
  edge="$(cdn_edge_addresses ${resolved} | tr '\n' ' ')"
  edge="${edge% }"
  if [ -n "${edge}" ]; then
    preflight_fail EDGE 11 "${role}" "${name} resolves to CDN edge address(es) ${edge}. A proxied record serves HTTP(S) only and cannot carry SSH; use the origin host's own record."
  fi
  echo "[PREFLIGHT] ${role} ${name} -> ${resolved}"

  for attempt in $(seq 1 "${SSH_READY_ATTEMPTS}"); do
    if err="$(ssh "${SSH_READY_OPTS[@]}" "${SSH_USER}@${address}" true 2>&1 >/dev/null)"; then
      echo "[PREFLIGHT] ${role} ${name}: SSH reachable and the deploy key is accepted."
      return 0
    fi
    last_err="$(printf '%s' "${err}" | tr '\n' ' ')"
    class="$(classify_ssh_error "${err}")"
    case "${class}" in
      DNS)
        preflight_fail DNS 10 "${role}" "ssh could not resolve ${address}: ${last_err}" ;;
      HOSTKEY)
        preflight_fail HOSTKEY 14 "${role}" "host key for ${address} does not match known_hosts: ${last_err}" ;;
      AUTH)
        auth_failures=$((auth_failures + 1))
        if (( auth_failures >= SSH_AUTH_ATTEMPTS )); then
          preflight_fail AUTH 13 "${role}" "${SSH_USER}@${address} rejected the deploy key ${auth_failures} times: ${last_err}. Confirm id_deploy is authorized on this host; the pipeline will not retry with other credentials."
        fi ;;
    esac
    if (( attempt < SSH_READY_ATTEMPTS )); then
      sleep "${SSH_READY_INTERVAL_SECONDS}"
    fi
  done
  if [ "${class}" = AUTH ]; then
    preflight_fail AUTH 13 "${role}" "${SSH_USER}@${address} still rejects the deploy key after ${SSH_READY_ATTEMPTS} attempts: ${last_err}"
  fi
  preflight_fail CONNECT 12 "${role}" "no SSH session to ${address} (${name}) after ${SSH_READY_ATTEMPTS} attempts; last error: ${last_err:-none}"
}

# Both endpoints are proven before either is staged, so a dead source can no
# longer leave a half-staged target behind.
preflight_endpoint source "${SOURCE_HOST}" "${SOURCE_ADDR}"
preflight_endpoint target "${TARGET_HOST}" "${TARGET_ADDR}"

# ------------------------------------------------------------------------------
# Step 0: stage migratectl on both hosts
# ------------------------------------------------------------------------------
echo "[STEP 0/5] Staging migratectl on both hosts..."
for h in "${SOURCE_ADDR}" "${TARGET_ADDR}"; do
  STAGED_ADDRS+=("${h}")
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${h}" "mkdir -p ${REMOTE_DIR} && chmod 700 ${REMOTE_DIR}"
  scp "${SSH_OPTS[@]}" -q "${MIGRATECTL_BIN}" "${SSH_USER}@${h}:${REMOTE_DIR}/migratectl"
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${h}" \
    "chmod +x ${REMOTE_DIR}/migratectl && ${REMOTE_DIR}/migratectl --help >/dev/null"
done
echo "[STEP 0/5] migratectl staged."

# Run migratectl inside the PostgreSQL container's netns so 127.0.0.1 reaches
# the database directly and pg_hba's `trust` line applies.
remote_migratectl() { # <host> <container> <args...>
  local host="$1" container="$2"; shift 2
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${host}" \
    "docker image inspect ${RUNTIME_IMAGE} >/dev/null 2>&1 || docker pull -q ${RUNTIME_IMAGE} >/dev/null;
     docker run --rm --network container:${container} \
       -v ${REMOTE_DIR}:/work ${RUNTIME_IMAGE} /work/migratectl $*"
}

# ------------------------------------------------------------------------------
# Step 1: export from PROD (read-only role)
# ------------------------------------------------------------------------------
echo "[STEP 1/5] Exporting snapshot on ${SOURCE_HOST}..."
EXPORT_ARGS="export --dsn postgres://${SOURCE_DB_USER}@127.0.0.1:5432/${DB_NAME}?sslmode=disable --output /work/snapshot.yaml"
[ -n "${EMAIL_FILTER}" ] && EXPORT_ARGS="${EXPORT_ARGS} --email ${EMAIL_FILTER}"
remote_migratectl "${SOURCE_ADDR}" "${SOURCE_CONTAINER}" ${EXPORT_ARGS}

# ------------------------------------------------------------------------------
# Step 2: move the snapshot host-to-host, never staging it on the runner
# ------------------------------------------------------------------------------
echo "[STEP 2/5] Transferring snapshot ${SOURCE_HOST} -> ${TARGET_HOST}..."
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SOURCE_ADDR}" "cat ${REMOTE_DIR}/snapshot.yaml" \
  | ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET_ADDR}" \
      "cat > ${REMOTE_DIR}/snapshot.yaml && chmod 600 ${REMOTE_DIR}/snapshot.yaml"

TRANSFERRED_BYTES="$(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${TARGET_ADDR}" "stat -c %s ${REMOTE_DIR}/snapshot.yaml")"
if [ "${TRANSFERRED_BYTES}" -le 0 ]; then
  echo "[ERROR] Transferred snapshot is empty." >&2
  exit 1
fi
echo "[STEP 2/5] Transferred ${TRANSFERRED_BYTES} bytes."

IMPORT_BASE="import --dsn postgres://${TARGET_DB_USER}@127.0.0.1:5432/${DB_NAME}?sslmode=disable --file /work/snapshot.yaml --regenerate-user-uuids --merge --merge-strategy timestamp"

# ------------------------------------------------------------------------------
# Step 3: dry-run preview on UAT
# ------------------------------------------------------------------------------
echo "[STEP 3/5] Dry-run import preview on ${TARGET_HOST}..."
remote_migratectl "${TARGET_ADDR}" "${TARGET_CONTAINER}" ${IMPORT_BASE} --dry-run

if [ "${DRY_RUN}" = "true" ]; then
  echo "[STEP 4/5] DRY_RUN=true. Skipping the write."
  echo "[STEP 5/5] Skipping verification (nothing was written)."
  echo "=========================================="
  echo " Dry-Run Completed Cleanly                "
  echo "=========================================="
  exit 0
fi

# ------------------------------------------------------------------------------
# Step 4: apply merge
# ------------------------------------------------------------------------------
echo "[STEP 4/5] Applying merge on ${TARGET_HOST}..."
remote_migratectl "${TARGET_ADDR}" "${TARGET_CONTAINER}" ${IMPORT_BASE}

# ------------------------------------------------------------------------------
# Step 5: convergence verification -- replaying the snapshot must be a no-op
# ------------------------------------------------------------------------------
if [ "${SKIP_VERIFY}" = "true" ]; then
  echo "[STEP 5/5] SKIP_VERIFY=true. Skipping post-import verification."
else
  echo "[STEP 5/5] Verifying convergence..."
  VERIFY_OUTPUT="$(remote_migratectl "${TARGET_ADDR}" "${TARGET_CONTAINER}" ${IMPORT_BASE} --dry-run 2>&1)"
  echo "${VERIFY_OUTPUT}"
  if ! grep -qE 'users inserted=0 updated=0' <<<"${VERIFY_OUTPUT}" ||
     ! grep -qE 'Identities inserted=0 updated=0' <<<"${VERIFY_OUTPUT}" ||
     ! grep -qE 'Sessions inserted=0 updated=0' <<<"${VERIFY_OUTPUT}"; then
    echo "[VERIFY FAILED] Snapshot rows are still missing in UAT after the merge." >&2
    exit 1
  fi
  echo "[STEP 5/5] Verification passed: UAT already contains every snapshot row."
fi

echo "=========================================="
echo " Migration Completed Cleanly              "
echo "=========================================="
