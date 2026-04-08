#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"
CADDYFILE="/etc/caddy/Caddyfile"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${API_PUBLIC_HOST:=api.svc.plus}"
: "${AI_GATEWAY_ACCESS_TOKEN:?missing AI_GATEWAY_ACCESS_TOKEN in ${ENV_FILE}}"

for file in "$COMPOSE_FILE" "$CADDYFILE"; do
  [[ -f "$file" ]] || {
    printf '[svc-ai-gateway] missing file: %s\n' "$file" >&2
    exit 1
  }
done

caddy validate --config "$CADDYFILE" >/dev/null
systemctl is-active --quiet caddy
docker compose -f "$COMPOSE_FILE" ps --status running apisix >/dev/null

curl --fail --silent --show-error \
  --resolve "${API_PUBLIC_HOST}:443:127.0.0.1" \
  -H "Authorization: Bearer ${AI_GATEWAY_ACCESS_TOKEN}" \
  "https://${API_PUBLIC_HOST}/v1/models" >/dev/null

printf '[svc-ai-gateway] healthcheck passed\n'
