#!/usr/bin/env bash
set -e

# ==============================================================================
# AI Workspace All-in-One Bootstrap Script
# ==============================================================================
# Usage:
#   curl -sfL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/setup-ai-workspace-all-in-one.sh | bash -
#
# Supported Environment Variables:
#   AI_WORKSPACE_SECURITY_LEVEL
#   LITELLM_API_CADDY_STRICT_WHITELIST
#   XWORKSPACE_CONSOLE_PUBLIC_ACCESS
#   XWORKMATE_BRIDGE_PUBLIC_ACCESS
#   GATEWAY_OPENCLAW_PUBLIC_ACCESS
#   VAULT_PUBLIC_ACCESS
#   XWORKSPACE_CONSOLE_ENABLE_XRDP
#   VAULT_PASS (Will be securely passed as vault password if set)
# ==============================================================================

REPO_URL=${REPO_URL:-"https://github.com/ai-workspace-infra/playbooks.git"}
BRANCH=${BRANCH:-"main"}
TARGET_DIR="/tmp/ai-workspace-deploy"

# Function: Output messages
info() {
    echo -e "\033[1;34m[INFO]\033[0m $*"
}
success() {
    echo -e "\033[1;32m[SUCCESS]\033[0m $*"
}
error() {
    echo -e "\033[1;31m[ERROR]\033[0m $*" >&2
    exit 1
}

info "Starting AI Workspace All-in-One Bootstrap..."

# 1. Install prerequisites (git, curl, ansible) if missing
if ! command -v ansible-playbook >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
    info "Installing required dependencies (git, ansible)..."
    if [ -f /etc/debian_version ]; then
        sudo apt-get update -y
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl software-properties-common
        sudo apt-add-repository --yes --update ppa:ansible/ansible
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ansible
    elif [ -f /etc/redhat-release ]; then
        sudo yum install -y epel-release
        sudo yum install -y git curl ansible
    else
        error "Unsupported OS. Please install git and ansible manually."
    fi
    success "Dependencies installed."
fi

# 2. Clone Repository
if [ -d "$TARGET_DIR" ]; then
    info "Updating existing repository in $TARGET_DIR..."
    cd "$TARGET_DIR"
    git fetch origin
    git reset --hard origin/"$BRANCH"
else
    info "Cloning playbooks repository to $TARGET_DIR..."
    git clone -b "$BRANCH" "$REPO_URL" "$TARGET_DIR"
    cd "$TARGET_DIR"
fi

# 3. Construct Ansible variables from Environment Variables
ANSIBLE_EXTRA_VARS=""

# Helper function to append to extra vars if set
append_var() {
    local env_name=$1
    local ansible_var=$2
    local val="${!env_name}"
    if [ -n "$val" ]; then
        info "Applying parameter: $ansible_var = $val"
        ANSIBLE_EXTRA_VARS="$ANSIBLE_EXTRA_VARS -e \"$ansible_var=$val\""
    fi
}

append_var "AI_WORKSPACE_SECURITY_LEVEL"        "ai_workspace_security_level"
append_var "LITELLM_API_CADDY_STRICT_WHITELIST" "litellm_api_caddy_strict_whitelist"
append_var "XWORKSPACE_CONSOLE_PUBLIC_ACCESS"   "xworkspace_console_public_access"
append_var "XWORKMATE_BRIDGE_PUBLIC_ACCESS"     "xworkmate_bridge_public_access"
append_var "GATEWAY_OPENCLAW_PUBLIC_ACCESS"     "gateway_openclaw_public_access"
append_var "VAULT_PUBLIC_ACCESS"                "vault_public_access"
append_var "XWORKSPACE_CONSOLE_ENABLE_XRDP"     "xworkspace_console_enable_xrdp"

# 4. Handle Vault Password
VAULT_OPT=""
if [ -n "$VAULT_PASS" ]; then
    VAULT_FILE=$(mktemp)
    echo "$VAULT_PASS" > "$VAULT_FILE"
    VAULT_OPT="--vault-password-file $VAULT_FILE"
    info "Vault password provided via environment."
fi

# 5. Run Ansible Playbook locally
info "Running Ansible Playbook locally..."
eval "ansible-playbook -i '127.0.0.1,' -c local setup-ai-workspace-all-in-one.yml $VAULT_OPT $ANSIBLE_EXTRA_VARS"
RET=$?

# Clean up vault file
if [ -n "$VAULT_OPT" ]; then
    rm -f "$VAULT_FILE"
fi

if [ $RET -eq 0 ]; then
    success "AI Workspace deployed successfully!"
else
    error "Deployment failed with exit code $RET."
fi
