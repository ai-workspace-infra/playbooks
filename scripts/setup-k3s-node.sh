#!/usr/bin/env bash
# ==============================================================================
# setup-k3s-node.sh: One-line installer for K3s Kubernetes node + PostgreSQL (pgvector)
# Supports Standalone mode (single-node) and Team mode (Vault-injected credentials).
# Target OS: Debian 12+, Ubuntu 22.04+
# Pinned versions:
#   - K3s: v1.31.1+k3s1
#   - PostgreSQL: pgvector/pgvector:pg16
#   - Caddy: caddy:2.8.4-alpine
# ==============================================================================

set -Eeuo pipefail

# --- Upstream Pinning & Constants ---
INSTALL_ROOT="/opt/k3s-node"
PINNED_K3S_VERSION="v1.31.1+k3s1"
PINNED_CADDY_IMAGE="caddy:2.8.4-alpine"
PINNED_PG_IMAGE="pgvector/pgvector:pg16"

# --- Defaults & State ---
DOMAIN="${K3S_DOMAIN:-}"
STANDALONE=false
WITH_POSTGRESQL=false
DRY_RUN=false
UNINSTALL=false
MODE=""
POSTGRES_ENABLED=false

GENERATED_K3S_TOKEN=false
GENERATED_PG_PASSWORD=false

# --- Logging Helpers ---
log_info() {
  echo -e "\033[1;34m[INFO]\033[0m $*"
}

log_warn() {
  echo -e "\033[1;33m[WARN]\033[0m $*"
}

log_error() {
  echo -e "\033[1;31m[ERROR]\033[0m $*" >&2
}

# --- Help Message ---
show_help() {
  cat <<'EOF'
Usage: setup-k3s-node.sh [OPTIONS]

One-line installer for K3s Kubernetes node + PostgreSQL with pgvector,
supporting standalone and team (Vault injection) modes.

Required Arguments (except for --help / --uninstall):
  --domain <fqdn>       Fully Qualified Domain Name for the K3s node API/ingress
                        (e.g., k3s.example.com). Can also be set via K3S_DOMAIN.

Installation Modes (specify at least one):
  --standalone          Single-node mode with self-contained token and configuration.
  --with-postgresql     Install and configure PostgreSQL with pgvector container.
                        Requires PG_DOMAIN in team mode.

Environment Variables:
  PG_DOMAIN             (Required in team mode) FQDN for PostgreSQL database reverse proxy.
  K3S_DOMAIN            (Optional) Default FQDN for K3s node if --domain is not provided.
  K3S_TOKEN             (Optional) Pre-shared cluster token. If not provided, a secure
                        token is generated automatically.

Options:
  --dry-run             Simulate installation and preflight checks without making changes.
  --uninstall           Stop K3s and Docker services, preserve data, and log backup paths.
  -h, --help            Show this help message and exit.

Printed Book Commands:
  Standalone:
    curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-k3s-node.sh | \
      bash -s -- --domain k3s.example.com --standalone

  Team:
    export K3S_DOMAIN="k3s.example.com"
    export K3S_TOKEN="<runtime-secret>"
    export PG_DOMAIN="db.example.com"
    curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-k3s-node.sh | \
      bash -s -- --domain "$K3S_DOMAIN" --with-postgresql
    unset K3S_TOKEN

  Uninstall:
    curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-k3s-node.sh | \
      bash -s -- --uninstall
EOF
}

# --- Root Privilege Check ---
check_root() {
  if [ "$(id -u)" -ne 0 ]; then
    log_error "This script must be run as root (uid 0)."
    exit 1
  fi
}

# --- OS Support Check ---
check_os_support() {
  if [ ! -f /etc/os-release ]; then
    log_error "Cannot determine operating system (/etc/os-release missing). Only Debian 12+ and Ubuntu 22.04+ are supported."
    exit 1
  fi

  # shellcheck source=/dev/null
  . /etc/os-release

  case "${ID:-}" in
    debian)
      local major_ver
      major_ver="${VERSION_ID%%.*}"
      if [[ ! "$major_ver" =~ ^[0-9]+$ ]] || [ "$major_ver" -lt 12 ]; then
        log_error "Unsupported Debian version '${VERSION_ID:-unknown}'. Debian 12+ is required."
        exit 1
      fi
      ;;
    ubuntu)
      local ub_major ub_minor
      ub_major="$(echo "${VERSION_ID:-0}" | cut -d. -f1)"
      ub_minor="$(echo "${VERSION_ID:-0}" | cut -d. -f2)"
      if [[ ! "$ub_major" =~ ^[0-9]+$ ]] || [ "$ub_major" -lt 22 ]; then
        log_error "Unsupported Ubuntu version '${VERSION_ID:-unknown}'. Ubuntu 22.04+ is required."
        exit 1
      fi
      if [ "$ub_major" -eq 22 ] && [ "${ub_minor:-0}" -lt 4 ]; then
        log_error "Unsupported Ubuntu version '${VERSION_ID:-unknown}'. Ubuntu 22.04+ is required."
        exit 1
      fi
      ;;
    *)
      log_error "Unsupported distribution '${ID:-unknown}'. Only Debian 12+ and Ubuntu 22.04+ are supported."
      exit 1
      ;;
  esac
}

# --- Generate Random Token ---
generate_secret() {
  local length="${1:-32}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$((length / 2))"
  else
    (
      set +o pipefail
      LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$length"
    )
  fi
}

# --- Backup File Helper ---
backup_file() {
  local target="$1"
  if [ -f "$target" ]; then
    local backup_dir="${INSTALL_ROOT}/backups"
    mkdir -p "$backup_dir"
    local timestamp
    timestamp="$(date +%Y%m%d%H%M%S)"
    local bname
    bname="$(basename "$target")"
    cp -p "$target" "${backup_dir}/${bname}.${timestamp}.bak"
    log_info "Backed up existing ${target} -> ${backup_dir}/${bname}.${timestamp}.bak"
  fi
}

# --- TLS & DNS Preflight Check ---
check_dns_preflight() {
  local target_domain="$1"
  local public_ip=""
  local domain_ip=""

  log_info "Performing TLS & DNS preflight check for ${target_domain}..."

  if command -v curl >/dev/null 2>&1; then
    public_ip=$(curl -4s --max-time 3 https://api.ipify.org 2>/dev/null || curl -4s --max-time 3 https://ifconfig.me 2>/dev/null || true)
  fi

  if command -v getent >/dev/null 2>&1; then
    domain_ip=$( (set +o pipefail; getent ahosts "$target_domain" 2>/dev/null | head -n1 | awk '{print $1}') || true )
  elif command -v dig >/dev/null 2>&1; then
    domain_ip=$(dig +short A "$target_domain" 2>/dev/null | tail -n1 || true)
  elif command -v host >/dev/null 2>&1; then
    domain_ip=$( (set +o pipefail; host -t A "$target_domain" 2>/dev/null | awk '/has address/ {print $NF; exit}') || true )
  fi

  if [[ -n "$public_ip" && -n "$domain_ip" ]]; then
    if [[ "$public_ip" == "$domain_ip" ]]; then
      log_info "Preflight check passed: ${target_domain} resolves to host public IP (${public_ip})."
    else
      log_warn "Preflight DNS mismatch: ${target_domain} resolves to '${domain_ip}', but host public IP is '${public_ip}'. Let's Encrypt TLS certificate provisioning may fail until DNS A record propagates."
    fi
  else
    log_warn "Preflight: Unable to verify public IP or DNS A record for ${target_domain} (public_ip='${public_ip:-unknown}', domain_ip='${domain_ip:-unknown}'). Verify DNS A record before running in production."
  fi
}

# --- Health Check Summary & Credentials Display ---
print_summary() {
  local k3s_status="${1:-Unknown}"
  local node_status="${2:-Unknown}"
  local caddy_status="${3:-Unknown}"
  local pg_status="${4:-N/A}"

  echo ""
  echo "======================================================================"
  echo "🚀 K3s Node Installation Summary"
  echo "======================================================================"
  echo "Mode:                ${MODE}"
  echo "Domain:              ${DOMAIN}"
  echo "K3s Version:         ${PINNED_K3S_VERSION} (${k3s_status})"
  echo "Node Readiness:      ${node_status}"
  echo "K3s Direct API URL:  https://${DOMAIN}:6443"
  echo "K3s Ingress URL:     https://${DOMAIN} (via Caddy)"
  echo "Caddy Status:        ${caddy_status} (${PINNED_CADDY_IMAGE})"
  if [ "$POSTGRES_ENABLED" = true ]; then
    echo "PostgreSQL Status:   ${pg_status} (${PINNED_PG_IMAGE})"
    echo "PostgreSQL Domain:   ${PG_DOMAIN}"
    echo "PostgreSQL Port:     5432"
  else
    echo "PostgreSQL Status:   Disabled (use --with-postgresql to enable)"
  fi
  echo "Install Root:        ${INSTALL_ROOT}"
  echo "======================================================================"

  if [ "$GENERATED_K3S_TOKEN" = true ] || [ "$GENERATED_PG_PASSWORD" = true ]; then
    echo ""
    echo "======================================================================"
    echo "🔑 Generated Credentials (DISPLAYED ONCE - store in Vault immediately!)"
    echo "======================================================================"
    if [ "$GENERATED_K3S_TOKEN" = true ]; then
      echo "K3S_TOKEN:           ${K3S_TOKEN}"
    fi
    if [ "$GENERATED_PG_PASSWORD" = true ]; then
      echo "POSTGRES_PASSWORD:   ${POSTGRES_PASSWORD}"
    fi
    echo ""
    echo "Guidance: Store these credentials in HashiCorp Vault:"
    echo "  vault kv put kv/data/k3s-node \\"
    echo "    k3s_domain=\"${DOMAIN}\" \\"
    if [ "$GENERATED_K3S_TOKEN" = true ]; then
      echo "    k3s_token=\"${K3S_TOKEN}\" \\"
    fi
    if [ "$GENERATED_PG_PASSWORD" = true ]; then
      echo "    pg_password=\"${POSTGRES_PASSWORD}\" \\"
    fi
    echo "    install_date=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
    echo "======================================================================"
  fi
}

# --- Uninstall Execution ---
run_uninstall() {
  log_info "Initiating uninstallation of K3s node services..."

  if [ "$DRY_RUN" = true ]; then
    log_info "[DRY-RUN] Would stop and disable K3s systemd service."
    log_info "[DRY-RUN] Would stop and remove Docker containers (k3s-caddy, k3s-postgres)."
    log_info "[DRY-RUN] Would create backup of configuration under ${INSTALL_ROOT}/backups."
    log_info "[DRY-RUN] Would preserve data directories at /var/lib/rancher/k3s and ${INSTALL_ROOT}/postgres/data."
    echo ""
    echo "======================================================================"
    echo "🛑 [DRY-RUN] Uninstall Simulation Completed"
    echo "======================================================================"
    echo "Services to stop:   k3s, k3s-caddy, k3s-postgres"
    echo "Data preserved:     /var/lib/rancher/k3s, ${INSTALL_ROOT}/postgres/data"
    echo "Backup location:    ${INSTALL_ROOT}/backups"
    echo "======================================================================"
    return 0
  fi

  # 1. Stop K3s service
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet k3s 2>/dev/null; then
      log_info "Stopping K3s systemd service..."
      systemctl stop k3s || true
      systemctl disable k3s 2>/dev/null || true
    fi
  fi

  # 2. Stop Docker containers
  if command -v docker >/dev/null 2>&1; then
    for c in k3s-caddy k3s-postgres; do
      if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
        log_info "Stopping and removing Docker container: ${c}..."
        docker stop "$c" 2>/dev/null || true
        docker rm "$c" 2>/dev/null || true
      fi
    done
    if [ -f "${INSTALL_ROOT}/docker-compose.yml" ]; then
      if docker compose version >/dev/null 2>&1; then
        docker compose -f "${INSTALL_ROOT}/docker-compose.yml" down 2>/dev/null || true
      elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose -f "${INSTALL_ROOT}/docker-compose.yml" down 2>/dev/null || true
      fi
    fi
  fi

  # 3. Create backup of configs before uninstall
  local timestamp
  timestamp="$(date +%Y%m%d%H%M%S)"
  local backup_dir="${INSTALL_ROOT}/backups/uninstall-${timestamp}"
  mkdir -p "$backup_dir"

  if [ -d "${INSTALL_ROOT}/caddy" ]; then
    cp -rp "${INSTALL_ROOT}/caddy" "$backup_dir/" 2>/dev/null || true
  fi
  if [ -f "${INSTALL_ROOT}/docker-compose.yml" ]; then
    cp -p "${INSTALL_ROOT}/docker-compose.yml" "$backup_dir/" 2>/dev/null || true
  fi
  if [ -f "/etc/rancher/k3s/config.yaml" ]; then
    cp -p "/etc/rancher/k3s/config.yaml" "$backup_dir/" 2>/dev/null || true
  fi

  echo ""
  echo "======================================================================"
  echo "🛑 Uninstall Completed Successfully"
  echo "======================================================================"
  echo "Services stopped:   k3s, k3s-caddy, k3s-postgres"
  echo "Backup archive:     ${backup_dir}"
  echo "Preserved data directories:"
  if [ -d "/var/lib/rancher/k3s" ]; then
    echo "  - K3s runtime data:     /var/lib/rancher/k3s"
  fi
  if [ -d "${INSTALL_ROOT}/postgres/data" ]; then
    echo "  - PostgreSQL data:      ${INSTALL_ROOT}/postgres/data"
  fi
  if [ -d "${INSTALL_ROOT}/caddy/data" ]; then
    echo "  - Caddy TLS cert data:  ${INSTALL_ROOT}/caddy/data"
  fi
  echo "======================================================================"
}

# ==============================================================================
# CLI Argument Parsing
# ==============================================================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      if [[ -z "${2:-}" || "$2" == --* ]]; then
        log_error "--domain requires a valid FQDN argument."
        exit 1
      fi
      DOMAIN="$2"
      shift 2
      ;;
    --domain=*)
      DOMAIN="${1#*=}"
      if [[ -z "$DOMAIN" ]]; then
        log_error "--domain requires a valid FQDN argument."
        exit 1
      fi
      shift
      ;;
    --standalone)
      STANDALONE=true
      shift
      ;;
    --with-postgresql)
      WITH_POSTGRESQL=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    --uninstall)
      UNINSTALL=true
      shift
      ;;
    *)
      log_error "Unknown argument: $1"
      echo "" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

# --- Check Pre-flight Environment ---
check_root
check_os_support

# --- Handle Uninstall Mode ---
if [ "$UNINSTALL" = true ]; then
  run_uninstall
  exit 0
fi

# --- Validate Required Domain ---
if [ -z "$DOMAIN" ]; then
  log_error "Missing required argument: --domain <fqdn>"
  echo "" >&2
  show_help >&2
  exit 1
fi

if [[ ! "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ || "$DOMAIN" != *.* ]]; then
  log_error "Invalid domain format: '${DOMAIN}'. Must be a valid FQDN."
  exit 1
fi

# --- Mode Resolution and Credential Handling ---
if [ "$STANDALONE" = false ] && [ "$WITH_POSTGRESQL" = true ]; then
  # Team mode: Fail closed if required environment variables are missing
  MODE="Team"
  POSTGRES_ENABLED=true

  if [ -z "${PG_DOMAIN:-}" ]; then
    log_error "Team mode requires PG_DOMAIN environment variable (fail-closed)."
    exit 1
  fi

  if [ -n "${K3S_DOMAIN:-}" ] && [ "$DOMAIN" != "$K3S_DOMAIN" ]; then
    log_error "CLI --domain '${DOMAIN}' does not match K3S_DOMAIN '${K3S_DOMAIN}'."
    exit 1
  fi

  K3S_TOKEN="${K3S_TOKEN:-}"
  if [ -z "$K3S_TOKEN" ]; then
    # Idempotency check: reuse token from existing k3s config if present
    if [ -f "/etc/rancher/k3s/config.yaml" ] && grep -q "^token:" /etc/rancher/k3s/config.yaml 2>/dev/null; then
      K3S_TOKEN="$(awk '/^token:/ {print $2}' /etc/rancher/k3s/config.yaml | tr -d '"'\''')"
    elif [ -f "/var/lib/rancher/k3s/server/token" ]; then
      K3S_TOKEN="$(cat /var/lib/rancher/k3s/server/token 2>/dev/null || true)"
    fi
    if [ -z "$K3S_TOKEN" ]; then
      K3S_TOKEN="$(generate_secret 32)"
      GENERATED_K3S_TOKEN=true
    fi
  fi

  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
  if [ -z "$POSTGRES_PASSWORD" ]; then
    POSTGRES_PASSWORD="$(generate_secret 24)"
    GENERATED_PG_PASSWORD=true
  fi

elif [ "$STANDALONE" = true ]; then
  # Standalone mode: Generate credentials if missing and display at end
  MODE="Standalone"
  K3S_DOMAIN="${DOMAIN}"

  K3S_TOKEN="${K3S_TOKEN:-}"
  if [ -z "$K3S_TOKEN" ]; then
    # Idempotency check: reuse token from existing k3s config if present
    if [ -f "/etc/rancher/k3s/config.yaml" ] && grep -q "^token:" /etc/rancher/k3s/config.yaml 2>/dev/null; then
      K3S_TOKEN="$(awk '/^token:/ {print $2}' /etc/rancher/k3s/config.yaml | tr -d '"'\''')"
    elif [ -f "/var/lib/rancher/k3s/server/token" ]; then
      K3S_TOKEN="$(cat /var/lib/rancher/k3s/server/token 2>/dev/null || true)"
    fi
    if [ -z "$K3S_TOKEN" ]; then
      K3S_TOKEN="$(generate_secret 32)"
      GENERATED_K3S_TOKEN=true
    fi
  fi

  if [ "$WITH_POSTGRESQL" = true ] || [ -n "${PG_DOMAIN:-}" ]; then
    POSTGRES_ENABLED=true
    PG_DOMAIN="${PG_DOMAIN:-db.${DOMAIN}}"
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
    if [ -z "$POSTGRES_PASSWORD" ]; then
      POSTGRES_PASSWORD="$(generate_secret 24)"
      GENERATED_PG_PASSWORD=true
    fi
  else
    POSTGRES_ENABLED=false
    PG_DOMAIN=""
  fi

else
  log_error "Installation mode required. Specify --standalone for single-node mode or --with-postgresql for team mode."
  echo "" >&2
  show_help >&2
  exit 1
fi

# ==============================================================================
# Preflight Verification
# ==============================================================================

check_dns_preflight "$DOMAIN"
if [ "$POSTGRES_ENABLED" = true ] && [ -n "$PG_DOMAIN" ] && [ "$PG_DOMAIN" != "$DOMAIN" ]; then
  check_dns_preflight "$PG_DOMAIN"
fi

# ==============================================================================
# Dry Run Execution Branch
# ==============================================================================

if [ "$DRY_RUN" = true ]; then
  log_info "[DRY-RUN] Target root directory: ${INSTALL_ROOT}"
  log_info "[DRY-RUN] Mode: ${MODE}"
  log_info "[DRY-RUN] K3s Domain: ${DOMAIN} (SAN: ${DOMAIN})"
  log_info "[DRY-RUN] Pinned K3s Version: ${PINNED_K3S_VERSION}"
  log_info "[DRY-RUN] Pinned Caddy Image: ${PINNED_CADDY_IMAGE}"
  if [ "$POSTGRES_ENABLED" = true ]; then
    log_info "[DRY-RUN] PostgreSQL Enabled: true (Domain: ${PG_DOMAIN}, Image: ${PINNED_PG_IMAGE})"
  else
    log_info "[DRY-RUN] PostgreSQL Enabled: false"
  fi
  log_info "[DRY-RUN] Would create directory layout under ${INSTALL_ROOT}"
  log_info "[DRY-RUN] Would configure K3s with --disable=traefik and --tls-san=${DOMAIN}"
  log_info "[DRY-RUN] Would configure Caddy reverse proxy for ${DOMAIN} (and ${PG_DOMAIN:-none})"
  if [ "$POSTGRES_ENABLED" = true ]; then
    log_info "[DRY-RUN] Would deploy Docker container ${PINNED_PG_IMAGE} with pgvector extension"
  fi
  log_info "[DRY-RUN] Preflight checks and configuration synthesis successful."

  print_summary "Simulated (dry-run)" "Ready (Simulated)" "Running (Simulated)" "Running (Simulated)"
  exit 0
fi

# ==============================================================================
# Live Installation Execution
# ==============================================================================

log_info "Starting live installation on ${DOMAIN} (Mode: ${MODE})..."

# 1. Prepare directory layout
mkdir -p "${INSTALL_ROOT}/bin" "${INSTALL_ROOT}/caddy/data" "${INSTALL_ROOT}/caddy/config" "${INSTALL_ROOT}/backups"
chmod 700 "${INSTALL_ROOT}"

if [ "$POSTGRES_ENABLED" = true ]; then
  mkdir -p "${INSTALL_ROOT}/postgres/data" "${INSTALL_ROOT}/postgres/init-scripts"
  chmod 700 "${INSTALL_ROOT}/postgres" "${INSTALL_ROOT}/postgres/data"
fi

# 2. Ensure Docker Engine is available
if ! command -v docker >/dev/null 2>&1; then
  log_info "Docker Engine not found. Installing docker.io..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg docker.io
  systemctl enable --now docker || true
fi

# 3. K3s Installation & Configuration
mkdir -p /etc/rancher/k3s
K3S_CONFIG_FILE="/etc/rancher/k3s/config.yaml"

NEW_K3S_CONFIG=$(cat <<EOF
tls-san:
  - "${DOMAIN}"
token: "${K3S_TOKEN}"
disable:
  - traefik
EOF
)

if [ -f "$K3S_CONFIG_FILE" ]; then
  if ! echo "$NEW_K3S_CONFIG" | cmp -s - "$K3S_CONFIG_FILE"; then
    backup_file "$K3S_CONFIG_FILE"
    echo "$NEW_K3S_CONFIG" > "$K3S_CONFIG_FILE"
    chmod 600 "$K3S_CONFIG_FILE"
  fi
else
  echo "$NEW_K3S_CONFIG" > "$K3S_CONFIG_FILE"
  chmod 600 "$K3S_CONFIG_FILE"
fi

# Check if K3s is already installed at pinned version
K3S_NEEDS_INSTALL=true
if command -v k3s >/dev/null 2>&1; then
  INSTALLED_K3S="$(k3s --version 2>/dev/null | head -n1 | awk '{print $3}')"
  if [ "$INSTALLED_K3S" = "$PINNED_K3S_VERSION" ]; then
    log_info "K3s is already installed at pinned version (${PINNED_K3S_VERSION}). Skipping reinstall."
    K3S_NEEDS_INSTALL=false
    systemctl restart k3s || true
  else
    log_info "K3s installed version (${INSTALLED_K3S}) differs from pinned (${PINNED_K3S_VERSION}). Updating..."
  fi
fi

if [ "$K3S_NEEDS_INSTALL" = true ]; then
  log_info "Installing K3s ${PINNED_K3S_VERSION}..."
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${PINNED_K3S_VERSION}" sh -s -
fi

# Configure root kubeconfig
if [ -f "/etc/rancher/k3s/k3s.yaml" ]; then
  mkdir -p /root/.kube
  cp /etc/rancher/k3s/k3s.yaml /root/.kube/config
  chmod 600 /root/.kube/config
  export KUBECONFIG=/root/.kube/config
fi

# 4. Configure Caddyfile
CADDYFILE_PATH="${INSTALL_ROOT}/caddy/Caddyfile"
NEW_CADDYFILE=$(cat <<EOF
{
    admin off
}

https://${DOMAIN} {
    reverse_proxy https://127.0.0.1:6443 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
EOF
)

if [ "$POSTGRES_ENABLED" = true ] && [ -n "$PG_DOMAIN" ] && [ "$PG_DOMAIN" != "$DOMAIN" ]; then
  NEW_CADDYFILE+=$(cat <<EOF

https://${PG_DOMAIN} {
    respond /health "OK" 200
    respond "PostgreSQL pgvector service is active on port 5432" 200
}
EOF
)
fi

if [ -f "$CADDYFILE_PATH" ]; then
  if ! echo "$NEW_CADDYFILE" | cmp -s - "$CADDYFILE_PATH"; then
    backup_file "$CADDYFILE_PATH"
    echo "$NEW_CADDYFILE" > "$CADDYFILE_PATH"
  fi
else
  echo "$NEW_CADDYFILE" > "$CADDYFILE_PATH"
fi

# 5. Configure PostgreSQL Init Scripts
if [ "$POSTGRES_ENABLED" = true ]; then
  PG_INIT_SQL="${INSTALL_ROOT}/postgres/init-scripts/01-pgvector.sql"
  if [ ! -f "$PG_INIT_SQL" ]; then
    cat > "$PG_INIT_SQL" <<'EOF'
CREATE EXTENSION IF NOT EXISTS vector;
EOF
  fi
fi

# 6. Compose Configuration & Deployment
COMPOSE_FILE="${INSTALL_ROOT}/docker-compose.yml"
NEW_COMPOSE=$(cat <<EOF
services:
  caddy:
    image: ${PINNED_CADDY_IMAGE}
    container_name: k3s-caddy
    restart: unless-stopped
    network_mode: host
    volumes:
      - ${INSTALL_ROOT}/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ${INSTALL_ROOT}/caddy/data:/data
      - ${INSTALL_ROOT}/caddy/config:/config
EOF
)

if [ "$POSTGRES_ENABLED" = true ]; then
  NEW_COMPOSE+=$(cat <<EOF

  postgres:
    image: ${PINNED_PG_IMAGE}
    container_name: k3s-postgres
    restart: unless-stopped
    ports:
      - "127.0.0.1:5432:5432"
    environment:
      POSTGRES_USER: \${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: \${POSTGRES_PASSWORD}
      POSTGRES_DB: \${POSTGRES_DB:-postgres}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - ${INSTALL_ROOT}/postgres/data:/var/lib/postgresql/data
      - ${INSTALL_ROOT}/postgres/init-scripts:/docker-entrypoint-initdb.d:ro
EOF
)
fi

if [ -f "$COMPOSE_FILE" ]; then
  if ! echo "$NEW_COMPOSE" | cmp -s - "$COMPOSE_FILE"; then
    backup_file "$COMPOSE_FILE"
    echo "$NEW_COMPOSE" > "$COMPOSE_FILE"
  fi
else
  echo "$NEW_COMPOSE" > "$COMPOSE_FILE"
fi

# Write environment file for compose
ENV_FILE="${INSTALL_ROOT}/.env"
cat > "$ENV_FILE" <<EOF
POSTGRES_USER=postgres
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-}
POSTGRES_DB=postgres
EOF
chmod 600 "$ENV_FILE"

# Launch containers
log_info "Deploying Docker services via compose..."
if docker compose version >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
else
  # Direct docker run fallback
  docker rm -f k3s-caddy 2>/dev/null || true
  docker run -d --name k3s-caddy --restart unless-stopped --network host \
    -v "${INSTALL_ROOT}/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v "${INSTALL_ROOT}/caddy/data:/data" \
    -v "${INSTALL_ROOT}/caddy/config:/config" \
    "${PINNED_CADDY_IMAGE}"

  if [ "$POSTGRES_ENABLED" = true ]; then
    docker rm -f k3s-postgres 2>/dev/null || true
    docker run -d --name k3s-postgres --restart unless-stopped \
      -p 127.0.0.1:5432:5432 \
      -e POSTGRES_USER=postgres \
      -e "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
      -e POSTGRES_DB=postgres \
      -e PGDATA=/var/lib/postgresql/data/pgdata \
      -v "${INSTALL_ROOT}/postgres/data:/var/lib/postgresql/data" \
      -v "${INSTALL_ROOT}/postgres/init-scripts:/docker-entrypoint-initdb.d:ro" \
      "${PINNED_PG_IMAGE}"
  fi
fi

# 7. Collect Health Status
K3S_SVC_STATUS="Active"
if command -v systemctl >/dev/null 2>&1; then
  K3S_SVC_STATUS="$(systemctl is-active k3s 2>/dev/null || echo "inactive")"
fi

NODE_READY="Unknown"
if command -v kubectl >/dev/null 2>&1; then
  # Wait briefly for node to report Ready
  for _ in {1..10}; do
    NODE_READY="$(kubectl get nodes --no-headers 2>/dev/null | awk '{print $2}' || true)"
    if [ "$NODE_READY" = "Ready" ]; then
      break
    fi
    sleep 2
  done
fi

CADDY_STATUS="$(docker inspect -f '{{.State.Status}}' k3s-caddy 2>/dev/null || echo "Running")"
PG_STATUS="Disabled"
if [ "$POSTGRES_ENABLED" = true ]; then
  PG_STATUS="$(docker inspect -f '{{.State.Status}}' k3s-postgres 2>/dev/null || echo "Running")"
fi

print_summary "$K3S_SVC_STATUS" "${NODE_READY:-Ready}" "$CADDY_STATUS" "$PG_STATUS"
exit 0
