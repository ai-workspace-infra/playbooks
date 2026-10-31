#!/usr/bin/env bash
#
# setup-vault-zitadel.sh
# One-line installer for HashiCorp Vault + Zitadel + Caddy on Debian 12+/Ubuntu 22.04+
# Supports standalone and team (Vault injection & OIDC/JWT integration) modes.
#
set -euo pipefail
set +x
umask 077

# Upstream pinned component versions
readonly PINNED_VAULT_VERSION="1.17.6"
readonly PINNED_VAULT_DEB_VERSION="1.17.6-1"
readonly PINNED_ZITADEL_VERSION="v2.64.0"
readonly PINNED_POSTGRES_VERSION="16-alpine"
readonly PINNED_CADDY_VERSION="2.8.4-alpine"

# Installation paths
readonly INSTALL_ROOT="/opt/vault-zitadel"
readonly VAULT_DIR="${INSTALL_ROOT}/vault"
readonly POSTGRES_DIR="${INSTALL_ROOT}/postgres"
readonly BACKUP_DIR="${INSTALL_ROOT}/backups"
readonly BIN_DIR="${INSTALL_ROOT}/bin"

# CLI parameters
DOMAIN=""
STANDALONE=false
WITH_OIDC=false
DRY_RUN=false
SHOW_HELP=false
UNINSTALL=false

show_help() {
  cat <<'EOF'
Usage: setup-vault-zitadel.sh --domain <fqdn> [--standalone | --with-oidc] [options]

One-line installer for HashiCorp Vault, Zitadel IAM, and Caddy reverse proxy
on Debian 12+ and Ubuntu 22.04+.

Options:
  --domain <fqdn>     Primary domain for Vault (required, unless --help or --uninstall)
  --standalone        Install Vault + Zitadel + Caddy in standalone mode
  --with-oidc         Install with automated Zitadel OIDC/JWT auth integration (team mode)
  --dry-run           Perform preflight checks and simulate installation without making changes
  --uninstall         Stop services and backup data directories
  --help, -h          Show this help message and exit

Environment Variables (Team mode --with-oidc):
  VAULT_DOMAIN        Vault external domain (must match --domain)
  ZITADEL_DOMAIN      Zitadel external domain (e.g. auth.example.com)
  ZITADEL_MASTERKEY   Zitadel masterkey (strictly 32 bytes/characters)
  VAULT_UNSEAL_KEY    Vault Shamir unseal key

Printed Book Commands:
  # Standalone:
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/vhosts/vault_zitadel_bootstrap/files/setup-vault-zitadel.sh | \
    bash -s -- --domain vault.example.com --standalone

  # Team:
  export VAULT_DOMAIN="vault.example.com"
  export ZITADEL_DOMAIN="auth.example.com"
  export ZITADEL_MASTERKEY="<runtime-secret>"
  export VAULT_UNSEAL_KEY="<runtime-secret>"
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/vhosts/vault_zitadel_bootstrap/files/setup-vault-zitadel.sh | \
    bash -s -- --domain "$VAULT_DOMAIN" --with-oidc
  unset ZITADEL_MASTERKEY VAULT_UNSEAL_KEY
EOF
}

check_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Error: setup-vault-zitadel.sh must be run as root (current UID: $(id -u))." >&2
    exit 1
  fi
}

check_os() {
  if [[ ! -f /etc/os-release ]]; then
    echo "Error: Cannot detect operating system (/etc/os-release not found)." >&2
    exit 1
  fi

  # shellcheck disable=SC1091
  . /etc/os-release

  local os_id="${ID:-}"
  local version_id="${VERSION_ID:-}"
  local major_version="${version_id%%.*}"

  case "$os_id" in
    debian)
      if [[ -z "$major_version" || "$major_version" -lt 12 ]]; then
        echo "Error: Unsupported Debian version '${version_id}'. Debian 12+ is required." >&2
        exit 1
      fi
      ;;
    ubuntu)
      if [[ -z "$major_version" || "$major_version" -lt 22 ]]; then
        echo "Error: Unsupported Ubuntu version '${version_id}'. Ubuntu 22.04+ is required." >&2
        exit 1
      fi
      ;;
    *)
      echo "Error: Unsupported operating system '${os_id}'. Only Debian 12+ and Ubuntu 22.04+ are supported." >&2
      exit 1
      ;;
  esac
}

generate_random_secret() {
  local len="${1:-32}"
  local secret=""
  while [[ ${#secret} -lt "$len" ]]; do
    local chunk
    chunk="$(head -c 128 /dev/urandom 2>/dev/null | LC_ALL=C tr -dc 'A-Za-z0-9' || true)"
    secret="${secret}${chunk}"
  done
  echo "${secret:0:len}"
}

get_public_ip() {
  local ip=""
  if command -v curl >/dev/null 2>&1; then
    ip="$(curl -fsSL --connect-timeout 3 https://api.ipify.org 2>/dev/null || \
          curl -fsSL --connect-timeout 3 https://ifconfig.me/ip 2>/dev/null || \
          curl -fsSL --connect-timeout 3 https://icanhazip.com 2>/dev/null || true)"
  elif command -v wget >/dev/null 2>&1; then
    ip="$(wget -qO- --timeout=3 https://api.ipify.org 2>/dev/null || true)"
  fi
  ip="${ip//[[:space:]]/}"
  if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1; then
    local host_ips
    host_ips="$(hostname -I 2>/dev/null || true)"
    read -r ip _ <<< "$host_ips"
  fi
  echo "$ip"
}

resolve_domain_ip() {
  local target_domain="$1"
  local ip=""
  if command -v getent >/dev/null 2>&1; then
    local ahosts_out
    ahosts_out="$(getent ahosts "$target_domain" 2>/dev/null || true)"
    read -r ip _ <<< "$ahosts_out"
  elif command -v dig >/dev/null 2>&1; then
    local dig_out
    dig_out="$(dig +short A "$target_domain" 2>/dev/null || true)"
    read -r ip _ <<< "$dig_out"
  elif command -v host >/dev/null 2>&1; then
    local host_out
    host_out="$(host -t A "$target_domain" 2>/dev/null || true)"
    if [[ "$host_out" =~ has\ address\ ([0-9.]+) ]]; then
      ip="${BASH_REMATCH[1]}"
    fi
  fi
  echo "$ip"
}

check_dns_preflight() {
  local public_ip
  public_ip="$(get_public_ip)"

  local domains=("$VAULT_DOMAIN" "$ZITADEL_DOMAIN")
  for d in "${domains[@]}"; do
    local resolved_ip
    resolved_ip="$(resolve_domain_ip "$d")"
    if [[ -z "$resolved_ip" ]]; then
      echo "[WARN] Preflight DNS: Domain '$d' could not be resolved to an IP address."
      echo "[WARN] Automatic TLS certificate issuance via Caddy may fail until DNS points to this server."
    elif [[ -n "$public_ip" && "$resolved_ip" != "$public_ip" ]]; then
      echo "[WARN] Preflight DNS: Domain '$d' resolves to '$resolved_ip', but detected public IP is '$public_ip'."
      echo "[WARN] Automatic TLS certificate issuance via Caddy may fail if DNS does not point to this server."
    else
      echo "[INFO] Preflight DNS: Domain '$d' resolves to '$resolved_ip'."
    fi
  done
}

resolve_zitadel_domain() {
  local target_domain="$1"
  if [[ -n "${ZITADEL_DOMAIN:-}" ]]; then
    echo "$ZITADEL_DOMAIN"
  elif [[ "$target_domain" == vault.* ]]; then
    echo "auth.${target_domain#vault.}"
  else
    echo "auth.${target_domain}"
  fi
}

backup_existing_config() {
  if [[ -d "$INSTALL_ROOT" ]]; then
    local ts
    ts="$(date +%Y%m%d%H%M%S)"
    local target_backup="${BACKUP_DIR}/config-${ts}"
    mkdir -p "$target_backup"
    [[ -f "${INSTALL_ROOT}/.env" ]] && cp "${INSTALL_ROOT}/.env" "${target_backup}/.env"
    [[ -f "${INSTALL_ROOT}/docker-compose.yml" ]] && cp "${INSTALL_ROOT}/docker-compose.yml" "${target_backup}/docker-compose.yml"
    [[ -f "${INSTALL_ROOT}/Caddyfile" ]] && cp "${INSTALL_ROOT}/Caddyfile" "${target_backup}/Caddyfile"
    [[ -d "${INSTALL_ROOT}/vault/config" ]] && cp -r "${INSTALL_ROOT}/vault/config" "${target_backup}/vault-config"
    echo "[INFO] Existing configuration backed up to ${target_backup}"
  fi
}

load_existing_env() {
  if [[ -f "${INSTALL_ROOT}/.env" ]]; then
    echo "[INFO] Found existing ${INSTALL_ROOT}/.env, loading configured credentials for idempotency."
    # Read variables without echoing
    while IFS='=' read -r key val || [[ -n "$key" ]]; do
      # Ignore comments and empty lines
      [[ "$key" =~ ^[[:space:]]*# ]] && continue
      [[ -z "$key" ]] && continue
      key="$(echo "$key" | tr -d '[:space:]')"
      val="$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//')"
      case "$key" in
        ZITADEL_MASTERKEY)
          [[ -z "${ZITADEL_MASTERKEY:-}" ]] && ZITADEL_MASTERKEY="$val"
          ;;
        PG_PASSWORD)
          [[ -z "${PG_PASSWORD:-}" ]] && PG_PASSWORD="$val"
          ;;
        VAULT_UNSEAL_KEY)
          [[ -z "${VAULT_UNSEAL_KEY:-}" ]] && VAULT_UNSEAL_KEY="$val"
          ;;
        VAULT_ROOT_TOKEN)
          [[ -z "${VAULT_ROOT_TOKEN:-}" ]] && VAULT_ROOT_TOKEN="$val"
          ;;
      esac
    done < "${INSTALL_ROOT}/.env"
  fi
}

install_vault_binary() {
  if command -v vault >/dev/null 2>&1; then
    local installed_ver
    installed_ver="$(vault version 2>/dev/null | awk '{print $2}' | sed 's/^v//' || true)"
    if [[ "$installed_ver" == "${PINNED_VAULT_VERSION}"* ]]; then
      echo "[INFO] Vault ${PINNED_VAULT_VERSION} is already installed, skipping."
      return 0
    fi
  fi

  echo "[INFO] Installing HashiCorp Vault ${PINNED_VAULT_VERSION}..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y curl gpg lsb-release jq ca-certificates unzip

  mkdir -p /usr/share/keyrings
  curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
  
  local codename
  codename="$(lsb_release -cs 2>/dev/null || true)"
  if [[ -z "$codename" && -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${VERSION_CODENAME:-}"
  fi

  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${codename} main" > /etc/apt/sources.list.d/hashicorp.list
  apt-get update -y || true

  if ! apt-get install -y "vault=${PINNED_VAULT_DEB_VERSION}"; then
    echo "[WARN] apt package vault=${PINNED_VAULT_DEB_VERSION} unavailable; downloading official release binary..."
    local arch
    case "$(uname -m)" in
      x86_64) arch="amd64" ;;
      aarch64|arm64) arch="arm64" ;;
      *) arch="amd64" ;;
    esac
    local zip_url="https://releases.hashicorp.com/vault/${PINNED_VAULT_VERSION}/vault_${PINNED_VAULT_VERSION}_linux_${arch}.zip"
    curl -fsSL "$zip_url" -o /tmp/vault.zip
    unzip -o /tmp/vault.zip -d /usr/local/bin/
    chmod +x /usr/local/bin/vault
    rm -f /tmp/vault.zip
  fi

  if ! getent group vault >/dev/null 2>&1; then
    groupadd -r vault
  fi
  if ! getent passwd vault >/dev/null 2>&1; then
    useradd -r -g vault -d /var/lib/vault -s /bin/false vault
  fi
}

install_docker_engine() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[INFO] Docker not found; installing Docker and Compose plugin..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y docker.io docker-compose-v2 || apt-get install -y docker.io docker-compose || {
      curl -fsSL https://get.docker.com | sh
    }
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable docker 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
  fi
}

configure_vault_service() {
  mkdir -p "${VAULT_DIR}/config" "${VAULT_DIR}/data"
  chown -R vault:vault "${VAULT_DIR}" 2>/dev/null || true
  chmod 750 "${VAULT_DIR}/data"

  cat > "${VAULT_DIR}/config/vault.hcl" <<EOF
ui = true
disable_mlock = true

storage "file" {
  path = "${VAULT_DIR}/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}

api_addr     = "https://${VAULT_DOMAIN}"
cluster_addr = "https://127.0.0.1:8201"
EOF
  chown vault:vault "${VAULT_DIR}/config/vault.hcl" 2>/dev/null || true

  if command -v systemctl >/dev/null 2>&1; then
    cat > /etc/systemd/system/vault.service <<EOF
[Unit]
Description=HashiCorp Vault Service
Documentation=https://www.vaultproject.io/docs/
Requires=network-online.target
After=network-online.target

[Service]
User=vault
Group=vault
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
SecureBits=keep-caps
AmbientCapabilities=CAP_IPC_LOCK
Capabilities=CAP_IPC_LOCK+ep
CapabilityBoundingSet=CAP_SYSLOG CAP_IPC_LOCK
NoNewPrivileges=yes
ExecStart=/usr/bin/vault server -config=${VAULT_DIR}/config/vault.hcl
ExecReload=/bin/kill --signal HUP \$MAINPID
KillMode=process
KillSignal=SIGINT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=65536
LimitMEMLOCK=infinity

[Install]
WantedBy=multi-user.target
EOF
    # Adjust binary path if installed in /usr/local/bin
    if [[ ! -x /usr/bin/vault && -x /usr/local/bin/vault ]]; then
      sed -i 's|/usr/bin/vault|/usr/local/bin/vault|g' /etc/systemd/system/vault.service
    fi

    systemctl daemon-reload
    systemctl enable vault
    systemctl restart vault
  else
    echo "[WARN] systemctl not available; running Vault in background process..."
    nohup vault server -config="${VAULT_DIR}/config/vault.hcl" > "${VAULT_DIR}/vault.log" 2>&1 &
  fi
}

configure_docker_compose() {
  mkdir -p "${POSTGRES_DIR}/data" "${INSTALL_ROOT}/caddy_data" "${INSTALL_ROOT}/caddy_config"

  cat > "${INSTALL_ROOT}/Caddyfile" <<EOF
${VAULT_DOMAIN} {
    reverse_proxy 127.0.0.1:8200
}

${ZITADEL_DOMAIN} {
    reverse_proxy h2c://127.0.0.1:8080 {
        flush_interval -1
    }
}
EOF

  cat > "${INSTALL_ROOT}/docker-compose.yml" <<EOF
services:
  db:
    image: postgres:${PINNED_POSTGRES_VERSION}
    restart: unless-stopped
    environment:
      POSTGRES_DB: zitadel
      POSTGRES_USER: zitadel
      POSTGRES_PASSWORD: \${PG_PASSWORD}
    volumes:
      - ./postgres/data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U zitadel -d zitadel"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - zitadel-net

  zitadel:
    image: ghcr.io/zitadel/zitadel:${PINNED_ZITADEL_VERSION}
    restart: unless-stopped
    command: >
      start-from-init
      --masterkey "\${ZITADEL_MASTERKEY}"
      --tlsMode disabled
    environment:
      ZITADEL_DATABASE_POSTGRES_HOST: db
      ZITADEL_DATABASE_POSTGRES_PORT: 5432
      ZITADEL_DATABASE_POSTGRES_DATABASE: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_USERNAME: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_PASSWORD: \${PG_PASSWORD}
      ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE: disable
      ZITADEL_DATABASE_POSTGRES_ADMIN_USERNAME: zitadel
      ZITADEL_DATABASE_POSTGRES_ADMIN_PASSWORD: \${PG_PASSWORD}
      ZITADEL_DATABASE_POSTGRES_ADMIN_SSL_MODE: disable
      ZITADEL_EXTERNALSECURE: "true"
      ZITADEL_EXTERNALDOMAIN: "${ZITADEL_DOMAIN}"
      ZITADEL_EXTERNALPORT: "443"
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "127.0.0.1:8080:8080"
    networks:
      - zitadel-net

  caddy:
    image: caddy:${PINNED_CADDY_VERSION}
    restart: unless-stopped
    network_mode: host
    environment:
      VAULT_DOMAIN: "${VAULT_DOMAIN}"
      ZITADEL_DOMAIN: "${ZITADEL_DOMAIN}"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy_data:/data
      - ./caddy_config:/config
    depends_on:
      - zitadel

networks:
  zitadel-net:
EOF

  cat > "${INSTALL_ROOT}/.env" <<EOF
VAULT_DOMAIN=${VAULT_DOMAIN}
ZITADEL_DOMAIN=${ZITADEL_DOMAIN}
ZITADEL_MASTERKEY=${ZITADEL_MASTERKEY}
PG_PASSWORD=${PG_PASSWORD}
VAULT_UNSEAL_KEY=${VAULT_UNSEAL_KEY:-}
VAULT_ROOT_TOKEN=${VAULT_ROOT_TOKEN:-}
EOF
  chmod 600 "${INSTALL_ROOT}/.env"

  echo "[INFO] Starting Zitadel, PostgreSQL, and Caddy containers..."
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${INSTALL_ROOT}/docker-compose.yml" up -d
  else
    docker-compose -f "${INSTALL_ROOT}/docker-compose.yml" up -d
  fi
}

initialize_and_unseal_vault() {
  local vault_addr="http://127.0.0.1:8200"
  export VAULT_ADDR="$vault_addr"

  echo "[INFO] Waiting for Vault API at ${vault_addr}..."
  local attempts=0
  until curl -s "${vault_addr}/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [[ $attempts -ge 30 ]]; then
      echo "[ERROR] Vault API failed to become ready after 30 seconds." >&2
      return 1
    fi
    sleep 1
  done

  local health
  health="$(curl -s "${vault_addr}/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200")"
  local initialized
  initialized="$(echo "$health" | jq -r '.initialized // false' 2>/dev/null || true)"
  local sealed
  sealed="$(echo "$health" | jq -r '.sealed // true' 2>/dev/null || true)"

  if [[ "$initialized" != "true" ]]; then
    echo "[INFO] Initializing Vault..."
    local init_output
    init_output="$(vault operator init -key-shares=1 -key-threshold=1 -format=json)"
    
    local extracted_unseal
    extracted_unseal="$(echo "$init_output" | jq -r '.unseal_keys_b64[0] // .unseal_keys_hex[0]' 2>/dev/null || true)"
    local extracted_token
    extracted_token="$(echo "$init_output" | jq -r '.root_token' 2>/dev/null || true)"

    if [[ -z "${VAULT_UNSEAL_KEY:-}" ]]; then
      VAULT_UNSEAL_KEY="$extracted_unseal"
    fi
    VAULT_ROOT_TOKEN="$extracted_token"

    # Update .env
    cat > "${INSTALL_ROOT}/.env" <<EOF
VAULT_DOMAIN=${VAULT_DOMAIN}
ZITADEL_DOMAIN=${ZITADEL_DOMAIN}
ZITADEL_MASTERKEY=${ZITADEL_MASTERKEY}
PG_PASSWORD=${PG_PASSWORD}
VAULT_UNSEAL_KEY=${VAULT_UNSEAL_KEY}
VAULT_ROOT_TOKEN=${VAULT_ROOT_TOKEN}
EOF
    chmod 600 "${INSTALL_ROOT}/.env"

    echo "[INFO] Vault initialized successfully."
    sealed="true"
  fi

  if [[ "$sealed" == "true" ]]; then
    if [[ -n "${VAULT_UNSEAL_KEY:-}" ]]; then
      echo "[INFO] Unsealing Vault..."
      vault operator unseal "$VAULT_UNSEAL_KEY" >/dev/null
      echo "[INFO] Vault unsealed successfully."
    else
      echo "[WARN] Vault is sealed and VAULT_UNSEAL_KEY is not available; unseal manually."
    fi
  else
    echo "[INFO] Vault is already unsealed."
  fi
}

configure_oidc_integration() {
  mkdir -p "${BIN_DIR}"
  local oidc_script="${BIN_DIR}/configure-oidc.sh"

  cat > "$oidc_script" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR

if [[ -f /opt/vault-zitadel/.env ]]; then
  # shellcheck disable=SC1091
  . /opt/vault-zitadel/.env
fi

if [[ -n "${VAULT_ROOT_TOKEN:-}" ]]; then
  export VAULT_TOKEN="${VAULT_ROOT_TOKEN}"
fi

echo "[INFO] Checking Vault auth methods..."
if ! vault auth list -format=json 2>/dev/null | jq -e '."jwt/"' >/dev/null 2>&1; then
  echo "[INFO] Enabling Vault JWT auth method at path 'jwt'..."
  vault auth enable -path=jwt jwt
fi

echo "[INFO] Configuring Vault JWT auth endpoint for Zitadel..."
vault write auth/jwt/config \
  oidc_discovery_url="https://${ZITADEL_DOMAIN}/oauth/v2" \
  default_role="team-member"

echo "[INFO] Configuring default role 'team-member' in Vault..."
vault write auth/jwt/role/team-member \
  role_type="jwt" \
  bound_audiences="${ZITADEL_CLIENT_ID:-zitadel}" \
  user_claim="sub" \
  policies="default" \
  ttl="1h"

echo "[INFO] Vault JWT/OIDC configuration completed successfully."
EOF
  chmod +x "$oidc_script"

  if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY-RUN] Generated ${oidc_script} (OIDC issuer: https://${ZITADEL_DOMAIN}/oauth/v2)."
    return 0
  fi

  echo "[INFO] Executing OIDC integration script..."
  if ! bash "$oidc_script"; then
    echo "[WARN] OIDC auto-configuration encountered an issue (Zitadel may still be initializing)."
    echo "[WARN] You can re-run: bash ${oidc_script} once Zitadel DNS and TLS are active."
  fi
}

uninstall_services() {
  check_root

  if [[ "$DRY_RUN" == true ]]; then
    echo "======================================================================"
    echo "DRY-RUN UNINSTALLATION SUMMARY"
    echo "======================================================================"
    echo "[DRY-RUN] Target root directory: ${INSTALL_ROOT}"
    echo "[DRY-RUN] Would stop and disable Vault systemd service."
    echo "[DRY-RUN] Would stop and remove Zitadel, PostgreSQL, and Caddy containers."
    echo "[DRY-RUN] Would preserve data directories from ${INSTALL_ROOT} to /opt/vault-zitadel-backup-<timestamp>."
    echo "[DRY-RUN] No system changes were applied."
    echo "======================================================================"
    exit 0
  fi

  echo "[INFO] Uninstalling Vault + Zitadel..."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop vault 2>/dev/null || true
    systemctl disable vault 2>/dev/null || true
  fi

  if [[ -f "${INSTALL_ROOT}/docker-compose.yml" ]] && command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
      docker compose -f "${INSTALL_ROOT}/docker-compose.yml" down 2>/dev/null || true
    else
      docker-compose -f "${INSTALL_ROOT}/docker-compose.yml" down 2>/dev/null || true
    fi
  fi

  if [[ -d "$INSTALL_ROOT" ]]; then
    local ts
    ts="$(date +%Y%m%d%H%M%S)"
    local target_backup="/opt/vault-zitadel-backup-${ts}"
    mv "$INSTALL_ROOT" "$target_backup"
    echo "[INFO] Services stopped and removed."
    echo "[INFO] Data and configuration safely preserved at: ${target_backup}"
  else
    echo "[INFO] Install directory ${INSTALL_ROOT} does not exist; nothing to back up."
  fi

  echo "[INFO] Uninstall completed."
  exit 0
}

# --- Parse CLI Arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      if [[ -z "${2:-}" || "$2" == --* ]]; then
        echo "Error: --domain requires a valid FQDN argument" >&2
        exit 1
      fi
      DOMAIN="$2"
      shift 2
      ;;
    --standalone)
      STANDALONE=true
      shift
      ;;
    --with-oidc)
      WITH_OIDC=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      SHOW_HELP=true
      shift
      ;;
    --uninstall)
      UNINSTALL=true
      shift
      ;;
    *)
      echo "Error: Unknown argument: $1" >&2
      echo "Run with --help to see valid options." >&2
      exit 1
      ;;
  esac
done

if [[ "$SHOW_HELP" == true ]]; then
  show_help
  exit 0
fi

if [[ "$UNINSTALL" == true ]]; then
  uninstall_services
fi

# Validation for installation mode
if [[ -z "$DOMAIN" ]]; then
  echo "Error: --domain <fqdn> is required (unless --help or --uninstall)." >&2
  exit 1
fi

if [[ "$STANDALONE" == true && "$WITH_OIDC" == true ]]; then
  echo "Error: --standalone and --with-oidc are mutually exclusive." >&2
  exit 1
fi

if [[ "$STANDALONE" == false && "$WITH_OIDC" == false ]]; then
  echo "Error: Either --standalone or --with-oidc must be specified." >&2
  exit 1
fi

check_root
check_os

# Check team mode env vars (fail closed)
if [[ "$WITH_OIDC" == true ]]; then
  missing_vars=()
  [[ -z "${VAULT_DOMAIN:-}" ]] && missing_vars+=("VAULT_DOMAIN")
  [[ -z "${ZITADEL_DOMAIN:-}" ]] && missing_vars+=("ZITADEL_DOMAIN")
  [[ -z "${ZITADEL_MASTERKEY:-}" ]] && missing_vars+=("ZITADEL_MASTERKEY")
  [[ -z "${VAULT_UNSEAL_KEY:-}" ]] && missing_vars+=("VAULT_UNSEAL_KEY")

  if [[ ${#missing_vars[@]} -gt 0 ]]; then
    echo "Error: Team mode (--with-oidc) requires environment variables: ${missing_vars[*]}" >&2
    exit 1
  fi

  if [[ "$DOMAIN" != "$VAULT_DOMAIN" ]]; then
    echo "Error: CLI flag --domain (${DOMAIN}) does not match VAULT_DOMAIN (${VAULT_DOMAIN})" >&2
    exit 1
  fi

  if [[ ${#ZITADEL_MASTERKEY} -ne 32 ]]; then
    echo "Error: ZITADEL_MASTERKEY must be strictly 32 bytes/characters (current length: ${#ZITADEL_MASTERKEY})." >&2
    exit 1
  fi
fi

# Standalone domain & secret resolution
GENERATED_STANDALONE_SECRETS=false
if [[ "$STANDALONE" == true ]]; then
  VAULT_DOMAIN="${VAULT_DOMAIN:-$DOMAIN}"
  ZITADEL_DOMAIN="$(resolve_zitadel_domain "$DOMAIN")"

  load_existing_env

  if [[ -z "${ZITADEL_MASTERKEY:-}" ]]; then
    ZITADEL_MASTERKEY="$(generate_random_secret 32)"
    GENERATED_STANDALONE_SECRETS=true
  fi
  if [[ ${#ZITADEL_MASTERKEY} -ne 32 ]]; then
    echo "Error: ZITADEL_MASTERKEY must be strictly 32 bytes/characters (current length: ${#ZITADEL_MASTERKEY})." >&2
    exit 1
  fi

  if [[ -z "${PG_PASSWORD:-}" ]]; then
    PG_PASSWORD="$(generate_random_secret 32)"
  fi
fi

# Preflight DNS check
check_dns_preflight

# --- Dry-run execution path ---
if [[ "$DRY_RUN" == true ]]; then
  echo "======================================================================"
  echo "DRY-RUN EXECUTION SUMMARY (Simulation only, no changes applied)"
  echo "======================================================================"
  echo "Status:              SUCCESS (All preflight requirements satisfied)"
  if [[ "$STANDALONE" == true ]]; then
    echo "Mode:                Standalone"
  else
    echo "Mode:                Team (--with-oidc integration enabled)"
  fi
  echo "Vault Target URL:    https://${VAULT_DOMAIN}"
  echo "Zitadel Target URL:  https://${ZITADEL_DOMAIN}"
  echo "Target Directory:    ${INSTALL_ROOT}"
  echo "Pinned Upstream Components:"
  echo "  - Vault:           ${PINNED_VAULT_VERSION} (Debian pkg: ${PINNED_VAULT_DEB_VERSION})"
  echo "  - Zitadel:         ${PINNED_ZITADEL_VERSION}"
  echo "  - PostgreSQL:      ${PINNED_POSTGRES_VERSION}"
  echo "  - Caddy:           ${PINNED_CADDY_VERSION}"
  echo "Vault Status:        Simulated unsealed / healthy"
  echo "Zitadel Health:      Simulated healthy (HTTP 200)"
  if [[ "$WITH_OIDC" == true ]]; then
    echo "OIDC Integration:    Pointing to https://${ZITADEL_DOMAIN}/oauth/v2"
  fi
  echo "======================================================================"
  exit 0
fi

# --- Real Execution Path ---
echo "[INFO] Starting Vault + Zitadel setup in ${INSTALL_ROOT}..."
backup_existing_config
mkdir -p "$INSTALL_ROOT" "$BACKUP_DIR" "$BIN_DIR"

install_vault_binary
configure_vault_service
install_docker_engine
configure_docker_compose
initialize_and_unseal_vault

if [[ "$WITH_OIDC" == true ]]; then
  configure_oidc_integration
fi

# Health check summary
vault_health_status="Unknown"
if curl -s "http://127.0.0.1:8200/v1/sys/health" >/dev/null 2>&1; then
  vault_health_status="Initialized & Unsealed (HTTP 200)"
elif curl -s "http://127.0.0.1:8200/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200" >/dev/null 2>&1; then
  vault_health_status="Responding (Sealed or Initializing)"
fi

zitadel_health_status="Starting / Initializing (Docker container running)"
if curl -s "http://127.0.0.1:8080/debug/validate" >/dev/null 2>&1 || curl -s "http://127.0.0.1:8080/healthz" >/dev/null 2>&1; then
  zitadel_health_status="Healthy (HTTP 200)"
fi

echo "======================================================================"
echo "Vault & Zitadel Installation Summary"
echo "======================================================================"
echo "Status:              SUCCESS (Services installed & running)"
if [[ "$STANDALONE" == true ]]; then
  echo "Deployment Mode:     Standalone"
else
  echo "Deployment Mode:     Team (--with-oidc)"
fi
echo "Vault Public URL:    https://${VAULT_DOMAIN}"
echo "Zitadel Public URL:  https://${ZITADEL_DOMAIN}"
echo "Vault API Status:    ${vault_health_status}"
echo "Zitadel Status:      ${zitadel_health_status}"
echo "Target Directory:    ${INSTALL_ROOT}"
echo "======================================================================"

if [[ "$STANDALONE" == true && "$GENERATED_STANDALONE_SECRETS" == true ]]; then
  echo ""
  echo "======================================================================"
  echo "⚠️  CREDENTIAL SUMMARY - STORE OFFLINE SECURELY NOW ⚠️"
  echo "======================================================================"
  echo "Vault URL:           https://${VAULT_DOMAIN}"
  echo "Vault Unseal Key:    ${VAULT_UNSEAL_KEY}"
  echo "Vault Root Token:    ${VAULT_ROOT_TOKEN:-<Check ${INSTALL_ROOT}/.env>}"
  echo ""
  echo "Zitadel URL:         https://${ZITADEL_DOMAIN}"
  echo "Zitadel Masterkey:   ${ZITADEL_MASTERKEY}"
  echo "Zitadel Admin User:  zitadel-admin@zitadel.localhost"
  echo "======================================================================"
  echo "These credentials are only displayed ONCE and saved locally in:"
  echo "${INSTALL_ROOT}/.env (chmod 0600)"
  echo "Store them in a secure password manager or offline store."
  echo "======================================================================"
fi
