#!/usr/bin/env bash
# XWorkmate Bridge Deployment Wrapper with Vault Integration
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-https://vault.svc.plus}"
export VAULT_ADDR

# 1. Check Vault connectivity
if ! command -v vault &> /dev/null; then
    echo "Error: vault CLI is not installed."
    exit 1
fi

# 2. Fetch secret
echo "[Vault] Fetching INTERNAL_SERVICE_TOKEN from accounts.svc.plus/details..."
# Attempt to get the token, fallback to current ENV if vault fails
INTERNAL_TOKEN=$(vault kv get -field=INTERNAL_SERVICE_TOKEN kv/accounts.svc.plus/details 2>/dev/null || echo "${INTERNAL_SERVICE_TOKEN:-}")

if [ -z "$INTERNAL_TOKEN" ]; then
    echo "Error: Could not retrieve token from Vault and INTERNAL_SERVICE_TOKEN is not set."
    exit 1
fi

# 3. Run Ansible
echo "[Ansible] Starting dry-run validation..."
cd "$(dirname "$0")/.."
ansible-playbook -i inventory.ini deploy_xworkmate_bridge_vhosts.yml \
  -l jp-xhttp-contabo.svc.plus \
  -e "INTERNAL_SERVICE_TOKEN=$INTERNAL_TOKEN" \
  -e "xworkmate_bridge_auth_token=$INTERNAL_TOKEN" \
  "$@"
