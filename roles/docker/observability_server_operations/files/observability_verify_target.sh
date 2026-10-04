#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_IP:?TARGET_IP is required}"
: "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH is required}"
: "${DASHBOARD_SOURCE:?DASHBOARD_SOURCE is required}"
: "${DNS_ACTION:=none}"
: "${VERIFY_SOURCE_NODE:=false}"
: "${SOURCE_IP:=}"
[[ "${TARGET_IP}" =~ ^[0-9.]+$ ]] || { echo "Target must be an IPv4 address." >&2; exit 2; }
[[ -d "${DASHBOARD_SOURCE}" ]] || { echo "Dashboard source directory is missing." >&2; exit 1; }
if [[ "${VERIFY_SOURCE_NODE}" == true ]]; then
  [[ "${SOURCE_IP}" =~ ^[0-9.]+$ && "${SOURCE_IP}" != "${TARGET_IP}" ]] || {
    echo "Source and target must be distinct IPv4 addresses for comparison." >&2; exit 2;
  }
fi

shopt -s nullglob
expected=("${DASHBOARD_SOURCE}"/*.json)
((${#expected[@]} > 0)) || { echo "No Git-managed dashboard JSON files found." >&2; exit 1; }
expected_manifest="$(cd "${DASHBOARD_SOURCE}" && for file in *.json; do sha256sum "${file}"; done | sort)"
ssh_args=(-i "${SSH_PRIVATE_KEY_PATH}" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15)

verify_node() {
  local ip="${1:?node IP required}" label="${2:?node label required}"
  echo "Checking ${label} node core services, Grafana auth, and dashboard assets."
  ssh "${ssh_args[@]}" "root@${ip}" bash -s <<'REMOTE'
set -euo pipefail
python3 - <<'PY'
import json, urllib.error, urllib.request
checks = {
    'VictoriaMetrics': 'http://127.0.0.1:9090/metrics',
    'VictoriaLogs': 'http://127.0.0.1:9428/metrics',
    'VictoriaTraces': 'http://127.0.0.1:10428/metrics',
}
for name, url in checks.items():
    with urllib.request.urlopen(url, timeout=8) as response:
        if response.status != 200:
            raise SystemExit(f'{name} metrics returned HTTP {response.status}')
for name in ('xstream_victoriametrics', 'xstream_victorialogs', 'xstream_victoriatraces', 'xstream_grafana'):
    with __import__('subprocess').Popen(['docker','inspect','--format','{{.State.Running}}',name], stdout=__import__('subprocess').PIPE, text=True) as proc:
        running = proc.communicate(timeout=8)[0].strip()
        if proc.returncode or running != 'true':
            raise SystemExit(f'{name} is not running')
with urllib.request.urlopen('http://127.0.0.1:3030/api/health', timeout=8) as response:
    health = json.load(response)
    if response.status != 200 or health.get('database') != 'ok':
        raise SystemExit('Grafana health/database check failed')
try:
    urllib.request.urlopen('http://127.0.0.1:3030/api/user', timeout=8)
except urllib.error.HTTPError as exc:
    if exc.code != 401:
        raise SystemExit(f'Grafana unauthenticated API returned HTTP {exc.code}; expected 401')
else:
    raise SystemExit('Grafana unauthenticated API was not rejected')
print('Core containers and metrics endpoints are healthy; Grafana database is OK and unauthenticated API is rejected.')
PY
cd /opt/observability-server/grafana/dashboards
for file in *.json; do test -f "${file}"; done
for file in *.json; do sha256sum "${file}"; done | sort
REMOTE
}

get_manifest() {
  local ip="${1:?node IP required}"
  ssh "${ssh_args[@]}" "root@${ip}" 'cd /opt/observability-server/grafana/dashboards && for file in *.json; do sha256sum "${file}"; done | sort'
}

verify_public_routes() {
  local ip="${1:?node IP required}" label="${2:?node label required}" path status
  local paths=(/grafana/api/health /vmetrics/metrics /vlogs/metrics /vtraces/metrics)
  echo "Checking HTTPS routes on ${label} node (${ip})."
  for path in "${paths[@]}"; do
    status="$(curl --connect-timeout 8 --max-time 20 --silent --show-error --output /dev/null \
      --write-out '%{http_code}' --resolve "observability.svc.plus:443:${ip}" \
      "https://observability.svc.plus${path}")"
    [[ "${status}" == 200 ]] || { echo "${label} ${path} returned HTTP ${status}, expected 200." >&2; exit 1; }
  done
  status="$(curl --connect-timeout 8 --max-time 20 --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --resolve "observability.svc.plus:443:${ip}" \
    https://observability.svc.plus/grafana/api/user)"
  [[ "${status}" == 401 ]] || { echo "${label} unauthenticated Grafana API returned HTTP ${status}, expected 401." >&2; exit 1; }
  echo "${label} HTTPS routes and Grafana authentication boundary passed."
}

verify_node "${TARGET_IP}" target
actual_target_manifest="$(get_manifest "${TARGET_IP}")"
if [[ "${actual_target_manifest}" != "${expected_manifest}" ]]; then
  echo "Target dashboard JSON set differs from the Git source." >&2
  diff -u <(printf '%s\n' "${expected_manifest}") <(printf '%s\n' "${actual_target_manifest}") || true
  exit 1
fi
verify_public_routes "${TARGET_IP}" target

if [[ "${VERIFY_SOURCE_NODE}" == true ]]; then
  verify_node "${SOURCE_IP}" source
  actual_source_manifest="$(get_manifest "${SOURCE_IP}")"
  if [[ "${actual_source_manifest}" != "${expected_manifest}" ]]; then
    echo "Source dashboard JSON set differs from the Git source." >&2
    diff -u <(printf '%s\n' "${expected_manifest}") <(printf '%s\n' "${actual_source_manifest}") || true
    exit 1
  fi
  [[ "${actual_source_manifest}" == "${actual_target_manifest}" ]] || {
    echo "Source and target dashboard JSON sets differ." >&2; exit 1;
  }
  verify_public_routes "${SOURCE_IP}" source
  echo "Source and target core services, Grafana authentication behavior, and Git-managed dashboards are consistent."
fi

if [[ "${DNS_ACTION}" == cutover ]]; then
  echo "Target core service checks passed; HTTPS will be checked after certificate issuance."
fi
