#!/usr/bin/env bash
set -euo pipefail
: "${WEB_SAAS_CANONICAL_PROBE_HOST:?WEB_SAAS_CANONICAL_PROBE_HOST is required}"
[[ "${WEB_SAAS_CANONICAL_PROBE_HOST}" =~ ^[A-Za-z0-9.-]+$ ]] || { echo 'Invalid canonical probe hostname.' >&2; exit 2; }

echo '--- Caddy container and published ports ---'
docker ps -a --filter 'name=^web-saas-caddy$' --format 'name={{.Names}} status={{.Status}} image={{.Image}} ports={{.Ports}}'
docker inspect web-saas-caddy --format 'state={{.State.Status}} running={{.State.Running}} restart_count={{.RestartCount}} started_at={{.State.StartedAt}}'
docker port web-saas-caddy

echo '--- host listeners on 80/443 ---'
ss -ltnp '( sport = :80 or sport = :443 )' || true
echo '--- Caddy configuration validation ---'
docker exec web-saas-caddy caddy validate --config /etc/caddy/Caddyfile
echo '--- local HTTPS probe using the environment hostname ---'
curl -k -sS -D - -o /dev/null --connect-timeout 5 --max-time 10 \
  --resolve "${WEB_SAAS_CANONICAL_PROBE_HOST}:443:127.0.0.1" \
  "https://${WEB_SAAS_CANONICAL_PROBE_HOST}/"
echo '--- recent Caddy logs (tokens redacted) ---'
docker logs --tail 160 web-saas-caddy 2>&1 | sed -E \
  -e 's/Bearer[[:space:]]+[^[:space:]]+/Bearer ***REDACTED***/g' \
  -e 's/gh[pousr]_[A-Za-z0-9_]+/***REDACTED_GITHUB_TOKEN***/g' \
  -e 's/hvs\.[A-Za-z0-9]+/***REDACTED_VAULT_TOKEN***/g' \
  -e 's/xox[baprs]-[A-Za-z0-9-]+/***REDACTED_SLACK_TOKEN***/g' || true
