#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

"${SCRIPT_DIR}/validate.sh"

if docker compose -f "$COMPOSE_FILE" ps --status running apisix >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" restart apisix
else
  docker compose -f "$COMPOSE_FILE" up -d apisix
fi

printf '[svc-ai-gateway] reload finished\n'
