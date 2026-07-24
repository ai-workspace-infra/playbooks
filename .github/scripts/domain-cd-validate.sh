#!/usr/bin/env bash
set -euo pipefail
: "${DOMAIN:?DOMAIN is required}"
: "${PLAYBOOK:?PLAYBOOK is required}"
: "${TARGET_HOST:?TARGET_HOST is required}"
test -f "${PLAYBOOK}"
case "${DOMAIN}:${PLAYBOOK}" in
  web-saas:setup-web-saas-domain.yml|ai-workspace:setup-ai-workspace-rootless.yml|agent-proxy:setup-agent-proxy-domain.yml|open-platform:setup-open-platform-domain.yml) ;;
  *) echo "Unsupported domain/playbook mapping: ${DOMAIN}:${PLAYBOOK}" >&2; exit 1 ;;
esac
