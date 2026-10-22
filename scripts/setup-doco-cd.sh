#!/usr/bin/env bash
# setup-doco-cd.sh - One-line installer for Doco CD & Caddy reverse proxy
# Target OS: Debian 12+, Ubuntu 22.04+
# Supports standalone demo mode and team (GitOps + Vault) mode.
set -euo pipefail
umask 022

readonly DOCO_CD_IMAGE="ghcr.io/kimdre/doco-cd:0.1.28"
readonly CADDY_IMAGE="caddy:2.8.4-alpine"
readonly INSTALL_ROOT="/opt/doco-cd"
readonly BACKUP_DIR="${INSTALL_ROOT}/backups"

log() {
    echo "[INFO] $*"
}

warn() {
    echo "[WARN] $*" >&2
}

error_exit() {
    echo "[ERROR] $*" >&2
    exit 1
}

show_help() {
    cat <<'EOF'
Usage: setup-doco-cd.sh --domain <fqdn> (--standalone | --env <name>) [options]
       setup-doco-cd.sh --uninstall [--dry-run]
       setup-doco-cd.sh --help

One-line installer for Doco CD continuous deployment controller and Caddy HTTPS reverse proxy.

Required Flags (Deployment):
  --domain <fqdn>       Fully Qualified Domain Name for Doco CD (e.g. cd.example.com).
                        Can also be provided via $DOCO_DOMAIN environment variable.
  --standalone          Deploy in standalone / demo mode (webhook-triggered deployments).
  --env <name>          Deploy in team mode for target environment (e.g. sit, uat, prod).
                        Requires GITOPS_REPO, GIT_ACCESS_TOKEN, WEBHOOK_SECRET.

Operational Flags:
  --dry-run             Simulate installation without making changes or starting containers.
  --uninstall           Stop Doco CD and Caddy, archive /opt/doco-cd and clean up.
  --help, -h            Show this help message and exit.

Environment Variables:
  DOCO_DOMAIN           Domain name for Doco CD (alternative to --domain).
  GITOPS_REPO           (Team mode) Git repository URL for GitOps declarations.
  GIT_ACCESS_TOKEN      (Team mode) Git authentication token / PAT for repository access.
  WEBHOOK_SECRET        Webhook verification secret. If omitted in standalone mode,
                        a secure random secret will be automatically generated.
  GITOPS_REF            (Team mode) Target git branch/ref (default: main).

Examples:
  # Standalone demo:
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-doco-cd.sh | \
    bash -s -- --domain cd.example.com --standalone

  # Team mode:
  export DOCO_DOMAIN="cd.example.com"
  export GITOPS_REPO="https://git.example.com/org/gitops.git"
  export GIT_ACCESS_TOKEN="<runtime-secret>"
  export WEBHOOK_SECRET="<runtime-secret>"
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-doco-cd.sh | \
    bash -s -- --domain "$DOCO_DOMAIN" --env uat
  unset GIT_ACCESS_TOKEN WEBHOOK_SECRET

  # Uninstall:
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-doco-cd.sh | \
    bash -s -- --uninstall
EOF
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error_exit "This script must be run as root (id -u == 0). Please run with sudo or as root."
    fi
}

check_os() {
    if [ ! -f /etc/os-release ]; then
        error_exit "/etc/os-release not found. Supported OS: Debian 12+ or Ubuntu 22.04+."
    fi

    local id="" version_id=""
    id="$(grep -E '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"'\')"
    version_id="$(grep -E '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '"'\')"

    case "$id" in
        debian)
            local deb_major="${version_id%%.*}"
            if [ -n "$deb_major" ] && [ "$deb_major" -lt 12 ] 2>/dev/null; then
                error_exit "Debian 12+ is required (detected Debian $version_id)."
            fi
            ;;
        ubuntu)
            local ub_major="${version_id%%.*}"
            if [ -n "$ub_major" ] && [ "$ub_major" -lt 22 ] 2>/dev/null; then
                error_exit "Ubuntu 22.04+ is required (detected Ubuntu $version_id)."
            fi
            ;;
        *)
            error_exit "Unsupported operating system: '${id:-unknown}'. Debian 12+ or Ubuntu 22.04+ is required."
            ;;
    esac
}

validate_domain() {
    local d="$1"
    if [[ ! "$d" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
        error_exit "Invalid domain name: '$d'. A valid FQDN is required (e.g. cd.example.com)."
    fi
}

check_dns_preflight() {
    local domain="$1"
    local public_ip=""
    local dns_ip=""

    # Attempt to resolve public IP
    if command -v curl >/dev/null 2>&1; then
        public_ip="$(curl -4 -fsSL --connect-timeout 3 https://api.ipify.org 2>/dev/null || curl -4 -fsSL --connect-timeout 3 https://ifconfig.me 2>/dev/null || true)"
    elif command -v wget >/dev/null 2>&1; then
        public_ip="$(wget -qO- --timeout=3 https://api.ipify.org 2>/dev/null || true)"
    fi

    # Attempt to resolve DNS A record
    if command -v getent >/dev/null 2>&1; then
        dns_ip="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | head -n 1 || true)"
    fi
    if [ -z "$dns_ip" ] && command -v dig >/dev/null 2>&1; then
        dns_ip="$(dig +short "$domain" A 2>/dev/null | head -n 1 || true)"
    fi
    if [ -z "$dns_ip" ] && command -v host >/dev/null 2>&1; then
        dns_ip="$(host -t A "$domain" 2>/dev/null | awk '/has address/ {print $4}' | head -n 1 || true)"
    fi
    if [ -z "$dns_ip" ] && command -v nslookup >/dev/null 2>&1; then
        dns_ip="$(nslookup "$domain" 2>/dev/null | awk '/^Address: / { print $2 }' | tail -n 1 || true)"
    fi

    if [ -z "$dns_ip" ]; then
        warn "Could not resolve DNS A record for '$domain'."
        warn "Ensure your DNS A record points to this host before Caddy automatic HTTPS can succeed."
    elif [ -n "$public_ip" ] && [ "$dns_ip" != "$public_ip" ]; then
        warn "DNS A record for '$domain' ($dns_ip) does not match detected public IP ($public_ip)."
        warn "Caddy automatic HTTPS (Let's Encrypt / ZeroSSL) may fail until DNS points to this host."
    else
        log "DNS preflight passed: '$domain' resolves to '${dns_ip:-$public_ip}'."
    fi
}

ensure_docker() {
    local dry_run="$1"

    local docker_installed=false
    local compose_installed=false

    if command -v docker >/dev/null 2>&1; then
        docker_installed=true
    fi
    if docker compose version >/dev/null 2>&1; then
        compose_installed=true
    fi

    if [ "$docker_installed" = true ] && [ "$compose_installed" = true ]; then
        log "Docker and Docker Compose plugin are already installed."
        return 0
    fi

    if [ "$dry_run" = true ]; then
        log "[DRY-RUN] Docker or Docker Compose plugin missing; would install docker-ce and docker-compose-plugin."
        return 0
    fi

    log "Installing Docker and Docker Compose plugin..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends ca-certificates curl gnupg

    if [ "$docker_installed" = false ]; then
        log "Installing Docker CE via official script..."
        curl -fsSL https://get.docker.com | sh
    fi

    if [ "$compose_installed" = false ]; then
        log "Installing Docker Compose plugin..."
        apt-get update -y
        apt-get install -y --no-install-recommends docker-compose-plugin
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable --now docker
    fi
}

render_caddyfile() {
    local domain="$1"
    cat <<EOF
${domain} {
    reverse_proxy app:80
}
EOF
}

render_compose() {
    local mode="$1"
    local webhook_secret="$2"
    local gitops_repo="$3"
    local git_token="$4"
    local target_env="$5"
    local gitops_ref="$6"

    cat <<EOF
services:
  app:
    container_name: doco-cd
    image: ${DOCO_CD_IMAGE}
    restart: unless-stopped
    ports:
      - "127.0.0.1:9080:80"
      - "127.0.0.1:9120:9120"
    environment:
      TZ: "Etc/UTC"
EOF

    if [ "$mode" = "team" ]; then
        cat <<EOF
      GIT_ACCESS_TOKEN: "${git_token}"
      WEBHOOK_SECRET: "${webhook_secret}"
      POLL_CONFIG: '[{"url":"${gitops_repo}","reference":"${gitops_ref}","target":"${target_env}","interval":60}]'
EOF
    else
        cat <<EOF
      WEBHOOK_SECRET: "${webhook_secret}"
EOF
    fi

    cat <<EOF
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - doco_data:/data
    cap_drop:
      - ALL
    healthcheck:
      test: ["CMD", "/doco-cd", "healthcheck"]
      start_period: 15s
      interval: 30s
      timeout: 5s
      retries: 3

  caddy:
    container_name: caddy
    image: ${CADDY_IMAGE}
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ${INSTALL_ROOT}/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - app

volumes:
  doco_data:
  caddy_data:
  caddy_config:
EOF
}

apply_config_file() {
    local dest="$1"
    local content="$2"
    local file_mode="$3"
    local dry_run="$4"

    local tmp_file
    tmp_file="$(mktemp)"
    printf '%s\n' "$content" > "$tmp_file"

    local changed=false
    if [ -f "$dest" ]; then
        if cmp -s "$dest" "$tmp_file"; then
            rm -f "$tmp_file"
            echo "unchanged"
            return 0
        else
            changed=true
        fi
    else
        changed=true
    fi

    if [ "$dry_run" = true ]; then
        rm -f "$tmp_file"
        echo "changed"
        return 0
    fi

    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ "$changed" = true ]; then
        mkdir -p "$BACKUP_DIR"
        local backup_path
        backup_path="${BACKUP_DIR}/$(basename "$dest").$(date +%Y%m%d%H%M%S)"
        cp -p "$dest" "$backup_path"
        log "Backed up existing $(basename "$dest") to $backup_path"
    fi

    cp "$tmp_file" "$dest"
    chmod "$file_mode" "$dest"
    rm -f "$tmp_file"

    echo "changed"
}

check_containers_running() {
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi
    local doco_img caddy_img doco_status caddy_status
    doco_img="$(docker inspect -f '{{.Config.Image}}' doco-cd 2>/dev/null || true)"
    caddy_img="$(docker inspect -f '{{.Config.Image}}' caddy 2>/dev/null || true)"
    doco_status="$(docker inspect -f '{{.State.Status}}' doco-cd 2>/dev/null || true)"
    caddy_status="$(docker inspect -f '{{.State.Status}}' caddy 2>/dev/null || true)"

    if [ "$doco_img" = "$DOCO_CD_IMAGE" ] && [ "$caddy_img" = "$CADDY_IMAGE" ] && \
       [ "$doco_status" = "running" ] && [ "$caddy_status" = "running" ]; then
        return 0
    fi
    return 1
}

handle_uninstall() {
    local dry_run="$1"
    log "Processing uninstall request..."

    if [ "$dry_run" = true ]; then
        log "[DRY-RUN] Would stop Doco CD and Caddy services via docker compose down."
        log "[DRY-RUN] Would archive $INSTALL_ROOT to /opt/doco-cd-backup-<timestamp>.tar.gz."
        log "[DRY-RUN] Would remove $INSTALL_ROOT directory."
        return 0
    fi

    if [ -d "$INSTALL_ROOT" ]; then
        if [ -f "$INSTALL_ROOT/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
            log "Stopping and removing Doco CD and Caddy containers..."
            docker compose -p doco-cd -f "$INSTALL_ROOT/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
        fi
        local backup_archive
        backup_archive="/opt/doco-cd-backup-$(date +%Y%m%d%H%M%S).tar.gz"
        log "Archiving $INSTALL_ROOT to $backup_archive..."
        tar -czf "$backup_archive" -C /opt doco-cd 2>/dev/null || true
        rm -rf "$INSTALL_ROOT"
        log "Uninstall completed successfully."
        log "Backup data directory saved to: $backup_archive"
    else
        log "Directory $INSTALL_ROOT does not exist; nothing to uninstall."
    fi
}

main() {
    local domain=""
    local mode=""
    local target_env=""
    local dry_run=false
    local uninstall=false

    # Parse command line flags
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --domain)
                if [ -z "${2:-}" ] || [[ "$2" == --* ]]; then
                    error_exit "--domain requires a valid FQDN argument."
                fi
                domain="$2"
                shift 2
                ;;
            --domain=*)
                domain="${1#*=}"
                if [ -z "$domain" ]; then
                    error_exit "--domain requires a valid FQDN argument."
                fi
                shift 1
                ;;
            --standalone)
                if [ -n "$mode" ] && [ "$mode" != "standalone" ]; then
                    error_exit "--standalone and --env are mutually exclusive."
                fi
                mode="standalone"
                shift 1
                ;;
            --env)
                if [ -z "${2:-}" ] || [[ "$2" == --* ]]; then
                    error_exit "--env requires an environment profile name (e.g. sit, uat, prod)."
                fi
                if [ -n "$mode" ] && [ "$mode" != "team" ]; then
                    error_exit "--standalone and --env are mutually exclusive."
                fi
                mode="team"
                target_env="$2"
                shift 2
                ;;
            --env=*)
                local env_val="${1#*=}"
                if [ -z "$env_val" ]; then
                    error_exit "--env requires an environment profile name (e.g. sit, uat, prod)."
                fi
                if [ -n "$mode" ] && [ "$mode" != "team" ]; then
                    error_exit "--standalone and --env are mutually exclusive."
                fi
                mode="team"
                target_env="$env_val"
                shift 1
                ;;
            --dry-run)
                dry_run=true
                shift 1
                ;;
            --uninstall)
                uninstall=true
                shift 1
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                error_exit "Unknown argument: '$1'. Use --help for usage information."
                ;;
        esac
    done

    # Basic root and OS checks
    check_root
    check_os

    # Handle uninstall workflow
    if [ "$uninstall" = true ]; then
        handle_uninstall "$dry_run"
        exit 0
    fi

    # Fallback domain from environment variable if flag omitted
    if [ -z "$domain" ] && [ -n "${DOCO_DOMAIN:-}" ]; then
        domain="$DOCO_DOMAIN"
    fi

    # Domain is required for installation
    if [ -z "$domain" ]; then
        error_exit "--domain <fqdn> is required (or set DOCO_DOMAIN environment variable)."
    fi
    validate_domain "$domain"
    DOCO_DOMAIN="$domain"

    # Mode validation
    if [ -z "$mode" ]; then
        error_exit "Deployment mode required. Specify either --standalone or --env <name>."
    fi

    local webhook_secret="${WEBHOOK_SECRET:-}"
    local gitops_repo="${GITOPS_REPO:-}"
    local git_token="${GIT_ACCESS_TOKEN:-}"
    local gitops_ref="${GITOPS_REF:-main}"
    local generated_secret=false

    # Team mode fail-closed checks
    if [ "$mode" = "team" ]; then
        local missing=0
        if [ -z "$gitops_repo" ]; then
            warn "Missing required environment variable: GITOPS_REPO"
            missing=1
        fi
        if [ -z "$git_token" ]; then
            warn "Missing required environment variable: GIT_ACCESS_TOKEN"
            missing=1
        fi
        if [ -z "$webhook_secret" ]; then
            warn "Missing required environment variable: WEBHOOK_SECRET"
            missing=1
        fi
        if [ -z "${DOCO_DOMAIN:-}" ]; then
            warn "Missing required variable: DOCO_DOMAIN (or --domain flag)"
            missing=1
        fi
        if [ "$missing" -ne 0 ]; then
            error_exit "Team mode (--env) failed closed due to missing required variables."
        fi
    else
        # Standalone mode: check if existing compose already has a webhook secret
        if [ -z "$webhook_secret" ] && [ -f "$INSTALL_ROOT/docker-compose.yml" ]; then
            local existing_secret
            existing_secret="$(grep -E '^\s*WEBHOOK_SECRET:' "$INSTALL_ROOT/docker-compose.yml" | head -n1 | sed -E 's/^\s*WEBHOOK_SECRET:\s*"?([^"]*)"?/\1/' || true)"
            if [ -n "$existing_secret" ]; then
                webhook_secret="$existing_secret"
            fi
        fi

        # Generate a strong secret if still empty
        if [ -z "$webhook_secret" ]; then
            if command -v openssl >/dev/null 2>&1; then
                webhook_secret="$(openssl rand -hex 24)"
            elif [ -r /dev/urandom ]; then
                webhook_secret="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
            else
                webhook_secret="$(date +%s%N | sha256sum | head -c 48)"
            fi
            generated_secret=true
        fi
    fi

    local profile="standalone"
    if [ "$mode" = "team" ]; then
        profile="$target_env"
    fi

    # DNS & IP Preflight check
    check_dns_preflight "$domain"

    # Docker & Docker Compose prerequisite check
    ensure_docker "$dry_run"

    # Render configurations
    local caddy_content compose_content
    caddy_content="$(render_caddyfile "$domain")"
    compose_content="$(render_compose "$mode" "$webhook_secret" "$gitops_repo" "$git_token" "$target_env" "$gitops_ref")"

    # Apply configuration files (with idempotency and backup)
    local caddy_status compose_status
    caddy_status="$(apply_config_file "$INSTALL_ROOT/Caddyfile" "$caddy_content" "0644" "$dry_run")"
    compose_status="$(apply_config_file "$INSTALL_ROOT/docker-compose.yml" "$compose_content" "0600" "$dry_run")"

    # Check if containers are already running and up-to-date
    local containers_running=false
    if check_containers_running; then
        containers_running=true
    fi

    if [ "$caddy_status" = "unchanged" ] && [ "$compose_status" = "unchanged" ] && [ "$containers_running" = true ]; then
        if [ "$dry_run" = true ]; then
            log "[DRY-RUN] Doco CD and Caddy configuration and running state are unchanged."
        else
            log "Doco CD and Caddy are already running with up-to-date configuration at pinned versions. Skipping container recreation."
        fi
    else
        if [ "$dry_run" = true ]; then
            log "[DRY-RUN] Would start or update Doco CD and Caddy services via docker compose."
        else
            log "Starting Doco CD and Caddy services..."
            docker compose -p doco-cd -f "$INSTALL_ROOT/docker-compose.yml" up -d
        fi
    fi

    # Health check & summary output
    if [ "$dry_run" = true ]; then
        cat <<EOF

======================================================================
  [DRY-RUN] Doco CD & Caddy Deployment Plan
======================================================================
  Status:              DRY-RUN (no changes made)
  URL:                 https://${domain}
  Environment Profile: ${profile}
  Webhook Endpoint:    https://${domain}/webhook
  Install Root:        ${INSTALL_ROOT}
  Doco CD Image:       ${DOCO_CD_IMAGE}
  Caddy Image:         ${CADDY_IMAGE}
======================================================================
EOF
        if [ "$generated_secret" = true ]; then
            log "[DRY-RUN] Standalone mode: Webhook secret would be generated automatically if not provided."
        fi
    else
        log "Waiting for services to become healthy..."
        local doco_health="running"
        for _ in {1..30}; do
            local h
            h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' doco-cd 2>/dev/null || true)"
            if [ "$h" = "healthy" ] || [ "$h" = "running" ]; then
                doco_health="$h"
                break
            fi
            sleep 1
        done

        cat <<EOF

======================================================================
  Doco CD & Caddy Deployment Summary
======================================================================
  Status:              HEALTHY (doco-cd: ${doco_health}, caddy: running)
  URL:                 https://${domain}
  Environment Profile: ${profile}
  Webhook Endpoint:    https://${domain}/webhook
  Install Root:        ${INSTALL_ROOT}
======================================================================
EOF

        if [ "$generated_secret" = true ]; then
            cat <<EOF

======================================================================
  [SECURITY] Standalone Webhook Secret Generated
======================================================================
  Webhook Secret: ${webhook_secret}

  IMPORTANT: This secret is displayed ONCE. Store it immediately in
  HashiCorp Vault (e.g. at kv/doco-cd or kv/CICD) or your secret manager.
======================================================================
EOF
        fi
    fi
}

main "$@"
