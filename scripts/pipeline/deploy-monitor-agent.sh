#!/bin/bash
set -eo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib/require-env.sh"

require_env MATRIX_HOST

# observability_endpoint is provided by workflow input, Vault config provides VECTOR_AUTH_USER and VECTOR_AUTH_PASSWORD.
# Fall back to standard defaults if Vault path kv/data/CICD/observability is missing or not configured.
VECTOR_AUTH_USER="${VECTOR_AUTH_USER:-observability}"
VECTOR_AUTH_PASSWORD="${VECTOR_AUTH_PASSWORD:-vector-auth-default-token}"

max_attempts="${MONITOR_AGENT_SSH_RETRIES:-10}"
for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  if ansible-playbook -i ../cmdb/inventory.ini deploy_observability_agent.yml \
    --limit "${MATRIX_HOST}" \
    -e "vector_observability_endpoint=${OBSERVABILITY_ENDPOINT}" \
    -e "vector_auth_user=${VECTOR_AUTH_USER}" \
    -e "vector_auth_password=${VECTOR_AUTH_PASSWORD}"; then
    exit 0
  fi

  rc=$?
  if [[ "${rc}" -ne 4 || "${attempt}" -eq "${max_attempts}" ]]; then
    exit "${rc}"
  fi

  echo "SSH to ${MATRIX_HOST} is temporarily unavailable; retrying monitor deployment (${attempt}/${max_attempts})..." >&2
  sleep 6
done
