#!/usr/bin/env bash
set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${DEPLOY_ENV:?DEPLOY_ENV is required}"
: "${DEPLOY_TAG:?DEPLOY_TAG is required}"
: "${MANAGED_IMAGES:?MANAGED_IMAGES is required}"

# By default, assume the gitops repository is checked out in 'gitops' relative to current directory
: "${GITOPS_DIR:=gitops}"
env_file="${GITOPS_DIR}/compose/${DOMAIN}/.env.${DEPLOY_ENV}"

if [[ ! -f "${env_file}" ]]; then
  echo "::error::GitOps environment file not found: ${env_file}"
  exit 1
fi

echo "Updating tags in ${env_file} to ${DEPLOY_TAG}..."

for var in ${MANAGED_IMAGES}; do
  # Check if the variable exists in the file
  if grep -qE "^${var}=" "${env_file}"; then
    # Replace everything after the colon (the tag) with the new deploy tag
    sed -i.bak -E "s|^(${var}=.*):.*$|\1:${DEPLOY_TAG}|" "${env_file}"
    rm -f "${env_file}.bak"
    echo "  - ${var} updated to ${DEPLOY_TAG}"
  else
    echo "::warning::Variable ${var} not found in ${env_file}"
  fi
done

echo "Update complete."
