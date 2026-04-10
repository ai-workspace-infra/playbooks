#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONFIG_FILE="${ROOT_DIR}/conf/config.yaml"
RULES_FILE="${ROOT_DIR}/conf/apisix.yaml"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

for file in "$CONFIG_FILE" "$RULES_FILE" "$COMPOSE_FILE"; do
  [[ -f "$file" ]] || {
    printf '[svc-ai-gateway] missing file: %s\n' "$file" >&2
    exit 1
  }
done

tail -n 1 "$RULES_FILE" | grep -q '^#END$' || {
  printf '[svc-ai-gateway] conf/apisix.yaml must end with #END for standalone reloads\n' >&2
  exit 1
}

docker compose -f "$COMPOSE_FILE" config >/dev/null
printf '[svc-ai-gateway] validation passed\n'
