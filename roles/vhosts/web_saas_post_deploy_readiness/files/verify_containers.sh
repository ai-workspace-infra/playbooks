#!/usr/bin/env bash
set -euo pipefail

timeout_seconds="${WEB_SAAS_CONTAINER_READY_TIMEOUT_SECONDS:-${1:-120}}"
poll_seconds="${WEB_SAAS_CONTAINER_READY_POLL_SECONDS:-${2:-3}}"
[[ "${timeout_seconds}" =~ ^[0-9]+$ && "${poll_seconds}" =~ ^[0-9]+$ ]] || {
  echo 'Container readiness timeout and poll interval must be non-negative integers.' >&2
  exit 2
}

required_containers=(
  web-saas-postgresql
  web-saas-stunnel-server
  web-saas-stunnel-client
  web-saas-accounts
  web-saas-xworkmate-bridge
  web-saas-billing
  web-saas-console
  web-saas-caddy
)

caddy_publishes_required_ports() {
  local port80 port443
  port80="$(docker inspect --format '{{range (index .NetworkSettings.Ports "80/tcp")}}{{.HostPort}} {{end}}' web-saas-caddy 2>/dev/null || true)"
  port443="$(docker inspect --format '{{range (index .NetworkSettings.Ports "443/tcp")}}{{.HostPort}} {{end}}' web-saas-caddy 2>/dev/null || true)"
  [[ -n "${port80//[[:space:]]/}" && -n "${port443//[[:space:]]/}" ]]
}

print_diagnostics() {
  echo '--- Web SaaS container state ---'
  docker ps -a --filter 'name=web-saas-' --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>&1 || true
  echo '--- Caddy host port bindings ---'
  docker inspect -f 'port_bindings={{json .NetworkSettings.Ports}} restart_count={{.RestartCount}}' web-saas-caddy 2>&1 || true
  echo '--- Doco-CD compose state ---'
  docker compose --project-name doco-cd -f /opt/doco-cd/docker-compose.yml ps 2>&1 || true
  echo '--- Doco-CD container state ---'
  docker inspect -f 'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restart_count={{.RestartCount}}' doco-cd 2>&1 || true
  echo '--- Doco-CD recent logs (tokens redacted) ---'
  docker logs --tail 200 doco-cd 2>&1 | sed -E \
    -e 's/Bearer[[:space:]]+[^[:space:]]+/Bearer ***REDACTED***/g' \
    -e 's/gh[pousr]_[A-Za-z0-9_]+/***REDACTED_GITHUB_TOKEN***/g' \
    -e 's/hvs\.[A-Za-z0-9]+/***REDACTED_VAULT_TOKEN***/g' \
    -e 's/xox[baprs]-[A-Za-z0-9-]+/***REDACTED_SLACK_TOKEN***/g' || true
}

deadline=$((SECONDS + timeout_seconds))
while :; do
  failed=()
  for container in "${required_containers[@]}"; do
    state="$(docker inspect --format '{{.State.Status}}' "${container}" 2>/dev/null || true)"
    if [[ "${state}" != running ]]; then failed+=("${container}=${state:-missing}"); fi
  done

  if ((${#failed[@]} == 0)) && caddy_publishes_required_ports; then
    echo 'Web SaaS containers and Caddy host port bindings are ready.'
    exit 0
  fi

  if ((SECONDS >= deadline)); then
    printf 'Readiness timed out; containers not ready: %s\n' "${failed[*]:-none}"
    print_diagnostics
    exit 1
  fi
  sleep "${poll_seconds}"
done
