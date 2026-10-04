#!/usr/bin/env bash
set -euo pipefail

timeout_seconds="${AGENT_PROXY_READY_TIMEOUT_SECONDS:-${1:-180}}"
poll_seconds="${AGENT_PROXY_READY_POLL_SECONDS:-${2:-5}}"
[[ "${timeout_seconds}" =~ ^[0-9]+$ && "${poll_seconds}" =~ ^[0-9]+$ ]] || {
  echo 'Agent Proxy readiness timeout and poll interval must be non-negative integers.' >&2
  exit 2
}

read -r -a files <<< "${AGENT_PROXY_RUNTIME_CONFIG_FILES:-/usr/local/etc/xray/config.json /usr/local/etc/xray/tcp-config.json}"
[[ "${#files[@]}" -gt 0 ]] || { echo 'No Agent Proxy runtime config paths were supplied.' >&2; exit 2; }
services=(caddy agent-proxy xray xray-tcp xray-exporter-xhttp xray-exporter-tcp)
for file in "${files[@]}"; do
  [[ -s "${file}" ]] || { echo "runtime_config=${file} state=missing-or-empty" >&2; exit 1; }
done

# Xray is started by the role's explicit systemd_service tasks before this
# check. Keeping this script read-only makes the readiness gate reusable.
deadline=$((SECONDS + timeout_seconds))
last_failure=''
while :; do
  failed=()
  for service in "${services[@]}"; do
    state="$(systemctl is-active "${service}" 2>/dev/null || true)"
    [[ "${state}" == active ]] || failed+=("${service}=${state:-unknown}")
  done
  if ((${#failed[@]} == 0)); then
    echo 'Agent Proxy final readiness passed.'
    exit 0
  fi
  last_failure="${failed[*]}"
  if ((SECONDS >= deadline)); then
    printf 'Agent Proxy readiness timed out: %s\n' "${last_failure}" >&2
    systemctl status agent-proxy --no-pager -l 2>&1 || true
    journalctl -u agent-proxy -n 120 --no-pager 2>&1 | sed -E \
      -e 's/Bearer[[:space:]]+[^[:space:]]+/Bearer ***REDACTED***/g' \
      -e 's/gh[pousr]_[A-Za-z0-9_]+/***REDACTED_GITHUB_TOKEN***/g' \
      -e 's/hvs\.[A-Za-z0-9]+/***REDACTED_VAULT_TOKEN***/g' || true
    ls -l "${files[@]}" 2>&1 || true
    exit 1
  fi
  sleep "${poll_seconds}"
done
