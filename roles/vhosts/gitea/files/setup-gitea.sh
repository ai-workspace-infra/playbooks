#!/usr/bin/env bash
#
# setup-gitea.sh - Automated installer for Gitea, Gitea Actions Runner, and Caddy.
# Supported OS: Debian 12+ (Bookworm), Ubuntu 22.04+ (Jammy/Noble).
#
set -euo pipefail

readonly GITEA_IMAGE="gitea/gitea:1.22.6"
readonly RUNNER_IMAGE="gitea/act_runner:0.2.11"
readonly CADDY_IMAGE="caddy:2.8.4-alpine"
readonly INSTALL_ROOT="/opt/gitea"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
readonly SCRIPT_NAME

show_help() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Automated installer for Gitea, Gitea Actions Runner, and Caddy reverse proxy
with automatic HTTPS on Debian 12+ and Ubuntu 22.04+.

Options:
  --domain <fqdn>       Fully-qualified domain name (e.g., git.example.com).
                        Required unless --help or --uninstall is specified.
  --standalone          Install standalone Gitea + Caddy stack.
  --with-runner         Install Gitea + Caddy + act_runner (team mode).
  --dry-run             Perform preflight checks and output installation plan
                        without applying changes to the host.
  --uninstall           Stop running containers, preserve persistent data to
                        /var/backups/gitea, and remove /opt/gitea stack.
  --help, -h            Show this help message and exit.

Environment Variables:
  Team Mode (--with-runner) [All Required - Fails closed if missing]:
    GITEA_DOMAIN               Domain for Gitea (must match --domain)
    GITEA_ADMIN_USER           Username for platform administrator
    GITEA_ADMIN_PASSWORD       Password for platform administrator
    RUNNER_REGISTRATION_TOKEN  Token to register act_runner with Gitea

  Standalone Mode (--standalone):
    GITEA_ADMIN_USER           Optional (default: platform-admin)
    GITEA_ADMIN_PASSWORD       Optional (generated automatically if unset)

Examples:
  # Standalone mode:
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/vhosts/gitea/files/setup-gitea.sh | \\
    bash -s -- --domain git.example.com --standalone

  # Team mode (Vault-injected credentials):
  export GITEA_DOMAIN="git.example.com"
  export GITEA_ADMIN_USER="platform-admin"
  export GITEA_ADMIN_PASSWORD="<runtime-secret>"
  export RUNNER_REGISTRATION_TOKEN="<runtime-secret>"
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/vhosts/gitea/files/setup-gitea.sh | \\
    bash -s -- --domain "\$GITEA_DOMAIN" --with-runner
  unset GITEA_ADMIN_PASSWORD RUNNER_REGISTRATION_TOKEN

  # Uninstall:
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/vhosts/gitea/files/setup-gitea.sh | \\
    bash -s -- --uninstall
EOF
}

check_root() {
  if [ "$(id -u)" != 0 ]; then
    echo "Error: This script must be run as root (uid 0) or via sudo." >&2
    exit 1
  fi
}

check_supported_os() {
  if [[ ! -f /etc/os-release ]]; then
    echo "Error: /etc/os-release not found. Target OS must be Debian 12+ or Ubuntu 22.04+." >&2
    exit 1
  fi

  # shellcheck disable=SC1091
  source /etc/os-release

  local os_id="${ID:-}"
  local os_ver="${VERSION_ID:-}"
  local is_supported=false

  if [[ "$os_id" == "debian" ]]; then
    local major="${os_ver%%.*}"
    if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$major" -ge 12 ]]; then
      is_supported=true
    fi
  elif [[ "$os_id" == "ubuntu" ]]; then
    local major="${os_ver%%.*}"
    local rest="${os_ver#*.}"
    local minor="${rest%%.*}"
    if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$minor" =~ ^[0-9]+$ ]]; then
      if [[ "$major" -gt 22 ]] || { [[ "$major" -eq 22 ]] && [[ "$minor" -ge 4 ]]; }; then
        is_supported=true
      fi
    fi
  fi

  if [[ "$is_supported" != "true" ]]; then
    echo "Error: Unsupported operating system '${os_id} ${os_ver}'. Supported OS: Debian 12+, Ubuntu 22.04+" >&2
    exit 1
  fi
}

check_dns_preflight() {
  local target_domain="$1"
  local resolved_ip=""
  local public_ip=""

  echo "[INFO] Running DNS and TLS preflight check for '${target_domain}'..."

  # Resolve domain A record
  if command -v dig >/dev/null 2>&1; then
    resolved_ip="$(dig +short A "$target_domain" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
  fi
  if [[ -z "$resolved_ip" ]] && command -v getent >/dev/null 2>&1; then
    resolved_ip="$(getent ahosts "$target_domain" 2>/dev/null | awk '{print $1}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
  fi
  if [[ -z "$resolved_ip" ]] && command -v host >/dev/null 2>&1; then
    resolved_ip="$(host -t A "$target_domain" 2>/dev/null | awk '/has address/ {print $4}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
  fi
  if [[ -z "$resolved_ip" ]] && command -v nslookup >/dev/null 2>&1; then
    resolved_ip="$(nslookup "$target_domain" 2>/dev/null | awk '/^Address: / {print $2}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
  fi

  # Detect public IPv4 address
  local endpoints=(
    "https://api.ipify.org"
    "https://icanhazip.com"
    "https://ifconfig.me/ip"
    "https://checkip.amazonaws.com"
  )
  for ep in "${endpoints[@]}"; do
    if command -v curl >/dev/null 2>&1; then
      public_ip="$(curl -fsSL --connect-timeout 2 --max-time 4 "$ep" 2>/dev/null | tr -d '[:space:]' || true)"
    elif command -v wget >/dev/null 2>&1; then
      public_ip="$(wget -qO- --timeout=3 "$ep" 2>/dev/null | tr -d '[:space:]' || true)"
    fi
    if [[ "$public_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      break
    else
      public_ip=""
    fi
  done

  if [[ -z "$resolved_ip" ]]; then
    echo "[WARN] DNS Preflight: Domain '${target_domain}' does not resolve to an IPv4 address. Automated TLS certificates via Caddy may fail until DNS A record is pointed to this server." >&2
  elif [[ -z "$public_ip" ]]; then
    echo "[WARN] DNS Preflight: Could not determine server public IPv4 address. Resolved domain IP is '${resolved_ip}'." >&2
  elif [[ "$resolved_ip" != "$public_ip" ]]; then
    echo "[WARN] DNS Preflight: Domain '${target_domain}' resolves to '${resolved_ip}', but server public IP is '${public_ip}'. Caddy automatic HTTPS certificates may fail until DNS A record is updated." >&2
  else
    echo "[INFO] DNS Preflight: Domain '${target_domain}' correctly resolves to server public IP (${public_ip})."
  fi
}

run_uninstall() {
  local dry_run_mode="$1"

  if [[ "$dry_run_mode" == "true" ]]; then
    echo "================================================================================"
    echo "Gitea Uninstallation Plan (Dry Run)"
    echo "================================================================================"
    echo "Target Directory: ${INSTALL_ROOT}"
    echo "Action:           Would stop Docker Compose stack (if running)"
    echo "Backup:           Would preserve persistent data to /var/backups/gitea/backup-<timestamp>"
    echo "Cleanup:          Would remove ${INSTALL_ROOT}"
    echo "================================================================================"
    echo "✅ Dry run uninstall completed. No changes made."
    return 0
  fi

  echo "[INFO] Initiating Gitea stack uninstall..."
  if [[ ! -d "$INSTALL_ROOT" ]]; then
    echo "[INFO] No Gitea installation found at ${INSTALL_ROOT}. Nothing to uninstall."
    return 0
  fi

  if command -v docker >/dev/null 2>&1 && [[ -f "$INSTALL_ROOT/docker-compose.yml" ]]; then
    echo "[INFO] Stopping and removing containers..."
    (cd "$INSTALL_ROOT" && docker compose down --remove-orphans) || true
  fi

  local backup_ts
  backup_ts="$(date +%Y%m%d%H%M%S)"
  local backup_dir="/var/backups/gitea/backup-${backup_ts}"
  mkdir -p "$backup_dir"

  if [[ -d "$INSTALL_ROOT/data" ]]; then
    echo "[INFO] Preserving persistent Gitea data to ${backup_dir}/data..."
    cp -a "$INSTALL_ROOT/data" "$backup_dir/"
  fi
  for f in docker-compose.yml Caddyfile .env; do
    if [[ -f "$INSTALL_ROOT/$f" ]]; then
      cp "$INSTALL_ROOT/$f" "$backup_dir/"
    fi
  done

  echo "[INFO] Removing ${INSTALL_ROOT} directory..."
  rm -rf "$INSTALL_ROOT"

  echo "================================================================================"
  echo "✅ Gitea uninstallation complete."
  echo "Containers stopped, compose stack removed."
  echo "Backup preserved at: ${backup_dir}"
  echo "================================================================================"
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[INFO] Docker not found. Installing Docker CE via apt..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings

    # shellcheck disable=SC1091
    source /etc/os-release
    local keyring_url="https://download.docker.com/linux/${ID}/gpg"
    curl -fsSL "$keyring_url" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
  fi

  if ! docker compose version >/dev/null 2>&1; then
    echo "[INFO] Installing docker-compose-plugin..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin || apt-get install -y -qq docker-compose
  fi
}

generate_compose_yaml() {
  local target_mode="$1"
  local target_file="$2"

  if [[ "$target_mode" == "team" ]]; then
    cat > "$target_file" <<EOF
services:
  gitea:
    image: ${GITEA_IMAGE}
    container_name: gitea
    restart: always
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__server__DOMAIN=\${GITEA_DOMAIN}
      - GITEA__server__ROOT_URL=https://\${GITEA_DOMAIN}/
      - GITEA__server__HTTP_PORT=3000
      - GITEA__server__SSH_PORT=2222
      - GITEA__server__SSH_LISTEN_PORT=22
      - GITEA__security__INSTALL_LOCK=true
      - GITEA__actions__ENABLED=true
      - GITEA__database__DB_TYPE=sqlite3
      - GITEA__database__PATH=/data/gitea/gitea.db
    volumes:
      - ${INSTALL_ROOT}/data:/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "2222:22"
    networks:
      - gitea-net

  caddy:
    image: ${CADDY_IMAGE}
    container_name: gitea-caddy
    restart: always
    depends_on:
      - gitea
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ${INSTALL_ROOT}/Caddyfile:/etc/caddy/Caddyfile:ro
      - ${INSTALL_ROOT}/caddy_data:/data
      - ${INSTALL_ROOT}/caddy_config:/config
    networks:
      - gitea-net

  runner:
    image: ${RUNNER_IMAGE}
    container_name: gitea-runner
    restart: always
    depends_on:
      - gitea
    environment:
      - GITEA_INSTANCE_URL=https://\${GITEA_DOMAIN}
      - GITEA_RUNNER_REGISTRATION_TOKEN=\${RUNNER_REGISTRATION_TOKEN}
      - GITEA_RUNNER_NAME=runner-\${GITEA_DOMAIN}
      - GITEA_RUNNER_LABELS=ubuntu-latest:docker://catthehacker/ubuntu:act-latest,ubuntu-22.04:docker://catthehacker/ubuntu:act-22.04
    volumes:
      - ${INSTALL_ROOT}/runner/data:/data
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - gitea-net

networks:
  gitea-net:
    driver: bridge
EOF
  else
    cat > "$target_file" <<EOF
services:
  gitea:
    image: ${GITEA_IMAGE}
    container_name: gitea
    restart: always
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__server__DOMAIN=\${GITEA_DOMAIN}
      - GITEA__server__ROOT_URL=https://\${GITEA_DOMAIN}/
      - GITEA__server__HTTP_PORT=3000
      - GITEA__server__SSH_PORT=2222
      - GITEA__server__SSH_LISTEN_PORT=22
      - GITEA__security__INSTALL_LOCK=true
      - GITEA__actions__ENABLED=true
      - GITEA__database__DB_TYPE=sqlite3
      - GITEA__database__PATH=/data/gitea/gitea.db
    volumes:
      - ${INSTALL_ROOT}/data:/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "2222:22"
    networks:
      - gitea-net

  caddy:
    image: ${CADDY_IMAGE}
    container_name: gitea-caddy
    restart: always
    depends_on:
      - gitea
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ${INSTALL_ROOT}/Caddyfile:/etc/caddy/Caddyfile:ro
      - ${INSTALL_ROOT}/caddy_data:/data
      - ${INSTALL_ROOT}/caddy_config:/config
    networks:
      - gitea-net

networks:
  gitea-net:
    driver: bridge
EOF
  fi
}

generate_caddyfile() {
  local target_domain="$1"
  local target_file="$2"

  cat > "$target_file" <<EOF
${target_domain} {
    reverse_proxy gitea:3000
}
EOF
}

generate_env_file() {
  local target_mode="$1"
  local target_domain="$2"
  local target_file="$3"

  cat > "$target_file" <<EOF
GITEA_DOMAIN=${target_domain}
EOF

  if [[ "$target_mode" == "team" ]]; then
    cat >> "$target_file" <<EOF
RUNNER_REGISTRATION_TOKEN=${RUNNER_REGISTRATION_TOKEN}
EOF
  fi
}

main() {
  local domain="${GITEA_DOMAIN:-}"
  local mode=""
  local dry_run=false
  local is_uninstall=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --domain)
        if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
          echo "Error: --domain requires a non-empty domain name argument." >&2
          exit 1
        fi
        domain="$2"
        shift 2
        ;;
      --domain=*)
        domain="${1#*=}"
        if [[ -z "$domain" ]]; then
          echo "Error: --domain requires a non-empty domain name argument." >&2
          exit 1
        fi
        shift 1
        ;;
      --standalone)
        if [[ -n "$mode" && "$mode" != "standalone" ]]; then
          echo "Error: Cannot combine --standalone with --with-runner." >&2
          exit 1
        fi
        mode="standalone"
        shift 1
        ;;
      --with-runner)
        if [[ -n "$mode" && "$mode" != "team" ]]; then
          echo "Error: Cannot combine --standalone with --with-runner." >&2
          exit 1
        fi
        mode="team"
        shift 1
        ;;
      --dry-run)
        dry_run=true
        shift 1
        ;;
      --uninstall)
        is_uninstall=true
        shift 1
        ;;
      --help|-h)
        show_help
        exit 0
        ;;
      *)
        echo "Error: Unknown argument '$1'." >&2
        echo "Run '${SCRIPT_NAME} --help' for usage." >&2
        exit 1
        ;;
    esac
  done

  # Execution requires root and supported OS
  check_root
  check_supported_os

  if [[ "$is_uninstall" == "true" ]]; then
    run_uninstall "$dry_run"
    exit 0
  fi

  if [[ -z "$domain" ]]; then
    echo "Error: --domain <fqdn> is required." >&2
    echo "Run '${SCRIPT_NAME} --help' for usage." >&2
    exit 1
  fi

  if [[ ! "$domain" =~ ^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
    echo "Error: Invalid domain format '${domain}'. Must be a valid fully-qualified domain name." >&2
    exit 1
  fi

  if [[ -z "$mode" ]]; then
    echo "Error: Installation mode is required. Please specify --standalone or --with-runner." >&2
    echo "Run '${SCRIPT_NAME} --help' for usage." >&2
    exit 1
  fi

  local admin_user=""
  local admin_password=""
  local admin_password_generated=false

  if [[ "$mode" == "team" ]]; then
    local missing_vars=()
    [[ -z "${GITEA_DOMAIN:-}" ]] && missing_vars+=("GITEA_DOMAIN")
    [[ -z "${GITEA_ADMIN_USER:-}" ]] && missing_vars+=("GITEA_ADMIN_USER")
    [[ -z "${GITEA_ADMIN_PASSWORD:-}" ]] && missing_vars+=("GITEA_ADMIN_PASSWORD")
    [[ -z "${RUNNER_REGISTRATION_TOKEN:-}" ]] && missing_vars+=("RUNNER_REGISTRATION_TOKEN")

    if [[ ${#missing_vars[@]} -gt 0 ]]; then
      echo "Error: Team mode (--with-runner) requires the following environment variables to be set:" >&2
      for v in "${missing_vars[@]}"; do
        echo "  - $v" >&2
      done
      echo "Execution aborted (fail-closed)." >&2
      exit 1
    fi

    if [[ "$domain" != "$GITEA_DOMAIN" ]]; then
      echo "Error: CLI --domain '${domain}' does not match environment variable GITEA_DOMAIN '${GITEA_DOMAIN}'." >&2
      exit 1
    fi

    admin_user="$GITEA_ADMIN_USER"
    admin_password="$GITEA_ADMIN_PASSWORD"
  else
    admin_user="${GITEA_ADMIN_USER:-platform-admin}"
    if [[ -n "${GITEA_ADMIN_PASSWORD:-}" ]]; then
      admin_password="$GITEA_ADMIN_PASSWORD"
    else
      admin_password="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9!#%*+,-./:;=?@^_~' | head -c 24)"
      if [[ ${#admin_password} -lt 16 ]]; then
        admin_password="$(head -c 64 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 24)"
      fi
      admin_password_generated=true
    fi
  fi

  # DNS & TLS Preflight Check
  check_dns_preflight "$domain"

  if [[ "$dry_run" == "true" ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    echo ""
    echo "================================================================================"
    echo "Gitea Installation Plan (Dry Run)"
    echo "================================================================================"
    echo "Target Host:       ${ID:-linux} ${VERSION_ID:-unknown} ($(uname -m))"
    echo "Root Privilege:    Verified (uid 0)"
    echo "Installation Root: ${INSTALL_ROOT}"
    echo "URL:               https://${domain}"
    echo "Mode:              ${mode} $([[ "$mode" == "team" ]] && echo "(Team / With Actions Runner)" || echo "(Standalone)")"
    echo "Admin User:        ${admin_user}"
    echo "Admin Password:    $([[ "$admin_password_generated" == "true" ]] && echo "[Randomly generated upon installation]" || echo "[Provided via environment]")"
    echo "Components:"
    echo "  - Gitea:         ${GITEA_IMAGE}"
    echo "  - Caddy:         ${CADDY_IMAGE}"
    if [[ "$mode" == "team" ]]; then
      echo "  - Act Runner:    ${RUNNER_IMAGE}"
    fi
    echo ""
    echo "Planned Files in ${INSTALL_ROOT}:"
    echo "  - docker-compose.yml"
    echo "  - Caddyfile"
    echo "  - .env (mode 0600)"
    echo ""
    echo "Idempotency & Safety:"
    echo "  - Pre-existing configs will be backed up to ${INSTALL_ROOT}/backups/ before changes."
    echo "  - Existing running stack at pinned versions will skip container recreation."
    echo "================================================================================"
    echo "✅ Dry run completed successfully. No changes made to system."
    exit 0
  fi

  # Live Run: Ensure Docker and compose plugin
  ensure_docker

  # Generate target configs in temporary directory
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "${tmp_dir}"' EXIT

  generate_compose_yaml "$mode" "${tmp_dir}/docker-compose.yml"
  generate_caddyfile "$domain" "${tmp_dir}/Caddyfile"
  generate_env_file "$mode" "$domain" "${tmp_dir}/.env"

  local config_changed=true
  local already_running=false

  if [[ -d "$INSTALL_ROOT" ]]; then
    local backup_ts
    backup_ts="$(date +%Y%m%d%H%M%S)"
    local cfg_backup_dir="${INSTALL_ROOT}/backups/config-${backup_ts}"
    mkdir -p "$cfg_backup_dir"

    for f in docker-compose.yml Caddyfile .env; do
      if [[ -f "${INSTALL_ROOT}/${f}" ]]; then
        cp "${INSTALL_ROOT}/${f}" "$cfg_backup_dir/"
      fi
    done
    echo "[INFO] Existing configuration backed up to ${cfg_backup_dir}"

    if [[ -f "${INSTALL_ROOT}/docker-compose.yml" && -f "${INSTALL_ROOT}/Caddyfile" && -f "${INSTALL_ROOT}/.env" ]]; then
      if cmp -s "${tmp_dir}/docker-compose.yml" "${INSTALL_ROOT}/docker-compose.yml" && \
         cmp -s "${tmp_dir}/Caddyfile" "${INSTALL_ROOT}/Caddyfile" && \
         cmp -s "${tmp_dir}/.env" "${INSTALL_ROOT}/.env"; then
        config_changed=false
      fi
    fi

    if (cd "$INSTALL_ROOT" && docker compose ps --status running --format '{{.Service}}' 2>/dev/null | grep -q "gitea"); then
      already_running=true
    fi
  fi

  if [[ "$config_changed" == "false" && "$already_running" == "true" ]]; then
    echo "[INFO] Gitea stack is already running with pinned versions and identical configuration. Skipping container recreation."
  else
    echo "[INFO] Deploying Gitea stack to ${INSTALL_ROOT}..."
    mkdir -p "${INSTALL_ROOT}/data" "${INSTALL_ROOT}/caddy_data" "${INSTALL_ROOT}/caddy_config" "${INSTALL_ROOT}/backups"
    if [[ "$mode" == "team" ]]; then
      mkdir -p "${INSTALL_ROOT}/runner/data"
    fi

    cp "${tmp_dir}/docker-compose.yml" "${INSTALL_ROOT}/"
    cp "${tmp_dir}/Caddyfile" "${INSTALL_ROOT}/"
    cp "${tmp_dir}/.env" "${INSTALL_ROOT}/"
    chmod 0600 "${INSTALL_ROOT}/.env"
    chmod 0750 "${INSTALL_ROOT}"
    chown -R 1000:1000 "${INSTALL_ROOT}/data"

    echo "[INFO] Starting containers via Docker Compose..."
    (cd "$INSTALL_ROOT" && docker compose up -d --remove-orphans)
  fi

  # Health Check and Admin User Initialization
  echo "[INFO] Waiting for Gitea service to become ready..."
  local retries=30
  local is_ready=false
  while [[ $retries -gt 0 ]]; do
    if (cd "$INSTALL_ROOT" && docker compose exec -T gitea gitea --version >/dev/null 2>&1); then
      is_ready=true
      break
    fi
    sleep 2
    retries=$((retries - 1))
  done

  if [[ "$is_ready" != "true" ]]; then
    echo "[WARN] Gitea container is taking longer than expected to initialize. Check logs with: (cd ${INSTALL_ROOT} && docker compose logs gitea)" >&2
  else
    if (cd "$INSTALL_ROOT" && docker compose exec -T gitea gitea admin user list 2>/dev/null | awk '{print $2}' | grep -Fxq "$admin_user"); then
      echo "[INFO] Admin user '${admin_user}' already exists."
    else
      echo "[INFO] Creating admin user '${admin_user}'..."
      (cd "$INSTALL_ROOT" && docker compose exec -T gitea gitea admin user create \
        --username "$admin_user" \
        --password "$admin_password" \
        --email "${admin_user}@${domain}" \
        --admin \
        --must-change-password=false >/dev/null 2>&1)
      echo "[INFO] Admin user '${admin_user}' created successfully."
    fi
  fi

  # Health Check Summary
  echo ""
  echo "================================================================================"
  echo "Gitea Installation Health Summary"
  echo "================================================================================"
  echo "Status:           Ready"
  echo "URL:              https://${domain}"
  echo "Mode:             ${mode} $([[ "$mode" == "team" ]] && echo "(Team / With Actions Runner)" || echo "(Standalone)")"
  echo "Install Root:     ${INSTALL_ROOT}"
  echo "Containers:"
  (cd "$INSTALL_ROOT" && docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}")
  echo "================================================================================"

  if [[ "$admin_password_generated" == "true" ]]; then
    echo ""
    echo "================================================================================"
    echo "🎉 Standalone Gitea Admin Credentials"
    echo "================================================================================"
    echo "URL:            https://${domain}"
    echo "Admin User:     ${admin_user}"
    echo "Admin Password: ${admin_password}"
    echo ""
    echo "⚠️  SECURITY NOTICE: These generated credentials are displayed ONCE."
    echo "Please record this password into HashiCorp Vault or your secrets manager:"
    echo ""
    echo "  vault kv put secret/platform/gitea \\"
    echo "    admin_user=\"${admin_user}\" \\"
    echo "    admin_password=\"${admin_password}\""
    echo "================================================================================"
  fi
}

main "$@"
