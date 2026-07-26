#!/usr/bin/env bash
set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${DEPLOY_ENV:?DEPLOY_ENV is required}"
: "${TARGET_HOST:?TARGET_HOST is required}"

echo "Validated ${DOMAIN}/${DEPLOY_ENV} delivery request."
echo "CD is pull-only: Doco-CD on ${TARGET_HOST} polls public ai-workspace-infra/gitops."
echo "No GitHub Actions step pushes GitOps changes."
if [[ -n "${DEPLOY_TAG:-}" ]]; then
  echo "Requested deployment ref: ${DEPLOY_TAG}"
fi
