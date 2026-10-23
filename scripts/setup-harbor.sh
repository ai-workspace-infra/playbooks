#!/usr/bin/env bash
# setup-harbor.sh - One-line installer for Harbor (self-hosted OCI artifact registry)
# Target OS: Debian 12+, Ubuntu 22.04+ (single node) or any host with kubectl + helm (Kubernetes)
# Modes:
#   --standalone   single node: official offline installer + harbor.yml + install.sh --with-trivy, Caddy HTTPS in front
#   --k8s          Kubernetes: official Helm chart (goharbor/harbor) with a rendered values file
set -euo pipefail
umask 022

readonly HARBOR_VERSION="${HARBOR_VERSION:-v2.15.4}"
readonly HARBOR_CHART_VERSION="${HARBOR_CHART_VERSION:-1.19.4}"
readonly CADDY_IMAGE="caddy:2.8.4-alpine"
readonly INSTALL_ROOT="/opt/harbor"
readonly DATA_DIR="${HARBOR_DATA_DIR:-/data/harbor}"
readonly HTTP_PORT="${HARBOR_HTTP_PORT:-8080}"
readonly RELEASE_URL="https://github.com/goharbor/harbor/releases/download/${HARBOR_VERSION}"
readonly HELM_REPO="https://helm.goharbor.io"

log()  { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
error_exit() { echo "[ERROR] $*" >&2; exit 1; }

show_help() {
    cat <<'HELP'
Usage: setup-harbor.sh --domain <fqdn> (--standalone | --k8s) [options]
       setup-harbor.sh --uninstall [--k8s] [--dry-run]
       setup-harbor.sh --help

Installs Harbor, the CNCF graduated OCI registry (images, Helm charts, OCI artifacts,
Trivy scanning, Cosign/Notation signatures, replication, proxy cache, retention).

Modes:
  --standalone          Single node: offline installer + harbor.yml + install.sh --with-trivy,
                        Harbor HTTP on 127.0.0.1:${HARBOR_HTTP_PORT:-8080}, Caddy automatic HTTPS in front.
  --k8s                 Kubernetes: helm install goharbor/harbor with rendered values
                        (ingress + TLS secret or cert-manager, PVC or S3 storage).

Options:
  --domain <fqdn>       Registry domain (e.g. harbor.example.com). Or $HARBOR_DOMAIN.
  --with-s3             Store blobs in S3-compatible object storage (needs HARBOR_S3_*).
  --external-db         Use external PostgreSQL + Redis/Valkey (needs HARBOR_DB_*, HARBOR_REDIS_ADDR).
  --namespace <ns>      Kubernetes namespace (default: harbor).
  --dry-run             Render configs and print actions, change nothing.
  --uninstall           Stop / remove Harbor; data directory is kept and archived config saved.
  --help, -h            Show this help.

Environment Variables:
  HARBOR_DOMAIN               Registry FQDN.
  HARBOR_ADMIN_PASSWORD       Initial admin password (<runtime-secret>; generated if omitted in --standalone).
  HARBOR_DB_PASSWORD          Internal DB password (generated if omitted).
  HARBOR_S3_ENDPOINT          e.g. https://s3.example.com      (with --with-s3)
  HARBOR_S3_BUCKET / HARBOR_S3_REGION / HARBOR_S3_ACCESS_KEY / HARBOR_S3_SECRET_KEY
  HARBOR_DB_HOST / HARBOR_DB_PORT / HARBOR_DB_USER / HARBOR_DB_NAME / HARBOR_DB_EXT_PASSWORD   (with --external-db)
  HARBOR_REDIS_ADDR           e.g. redis.example.com:6379      (with --external-db)
  HARBOR_TLS_SECRET           (k8s) existing TLS secret name; otherwise chart certSource=auto.
  HARBOR_VERSION / HARBOR_CHART_VERSION   Pinned: v2.15.4 / 1.19.4.

Printed Book Commands:
  # Single node demo
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-harbor.sh | \
    bash -s -- --domain harbor.example.com --standalone

  # Team: S3-compatible storage, credentials from Vault at runtime
  export HARBOR_DOMAIN="harbor.example.com"
  export HARBOR_ADMIN_PASSWORD="<runtime-secret>"
  export HARBOR_S3_ENDPOINT="https://s3.example.com"
  export HARBOR_S3_BUCKET="harbor"
  export HARBOR_S3_ACCESS_KEY="<runtime-secret>"
  export HARBOR_S3_SECRET_KEY="<runtime-secret>"
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-harbor.sh | \
    bash -s -- --domain "$HARBOR_DOMAIN" --standalone --with-s3
  unset HARBOR_ADMIN_PASSWORD HARBOR_S3_ACCESS_KEY HARBOR_S3_SECRET_KEY

  # Kubernetes (K3s/K8s)
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-harbor.sh | \
    bash -s -- --domain harbor.example.com --k8s --with-s3
HELP
}

MODE=""; DOMAIN="${HARBOR_DOMAIN:-}"; WITH_S3=false; EXT_DB=false; NS="harbor"; DRY=false; UNINSTALL=false
while [ $# -gt 0 ]; do
    case "$1" in
        --domain) DOMAIN="${2:-}"; shift ;;
        --standalone) MODE=standalone ;;
        --k8s) MODE=k8s ;;
        --with-s3) WITH_S3=true ;;
        --external-db) EXT_DB=true ;;
        --namespace) NS="${2:-}"; shift ;;
        --dry-run) DRY=true ;;
        --uninstall) UNINSTALL=true ;;
        --help|-h) show_help; exit 0 ;;
        *) error_exit "Unknown flag: $1 (see --help)" ;;
    esac
    shift
done

run() { if [ "$DRY" = true ]; then echo "[DRY-RUN] $*"; else "$@"; fi; }
gen_secret() { head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24; }
need() { [ -n "${!1:-}" ] || error_exit "$1 is required for this mode."; }

validate_domain() {
    [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]] || \
        error_exit "Invalid domain '$1' (e.g. harbor.example.com)."
}

check_root() { [ "$(id -u)" -eq 0 ] || [ "$DRY" = true ] || error_exit "Run as root for --standalone."; }

check_inputs() {
    if [ "$WITH_S3" = true ]; then
        for v in HARBOR_S3_ENDPOINT HARBOR_S3_BUCKET HARBOR_S3_ACCESS_KEY HARBOR_S3_SECRET_KEY; do need "$v"; done
    fi
    if [ "$EXT_DB" = true ]; then
        for v in HARBOR_DB_HOST HARBOR_DB_USER HARBOR_DB_EXT_PASSWORD HARBOR_REDIS_ADDR; do need "$v"; done
    fi
}

# ---------- single node ----------
render_harbor_yml() {
    cat <<YML
hostname: ${DOMAIN}
http:
  port: ${HTTP_PORT}
# TLS terminates at Caddy in front of Harbor; clients always use https://${DOMAIN}
external_url: https://${DOMAIN}
harbor_admin_password: ${ADMIN_PW}
database:
  password: ${DB_PW}
  max_idle_conns: 100
  max_open_conns: 900
data_volume: ${DATA_DIR}
trivy:
  ignore_unfixed: false
  skip_update: false
  offline_scan: false
  security_check: vuln
  insecure: false
jobservice:
  max_job_workers: 10
  job_loggers:
    - STD_OUTPUT
    - FILE
notification:
  webhook_job_max_retry: 3
log:
  level: info
  local:
    rotate_count: 50
    rotate_size: 200M
    location: /var/log/harbor
_version: 2.15.0
upload_purging:
  enabled: true
  age: 168h
  interval: 24h
  dryrun: false
YML
    if [ "$WITH_S3" = true ]; then cat <<YML
storage_service:
  s3:
    region: ${HARBOR_S3_REGION:-us-east-1}
    bucket: ${HARBOR_S3_BUCKET}
    accesskey: ${HARBOR_S3_ACCESS_KEY}
    secretkey: ${HARBOR_S3_SECRET_KEY}
    regionendpoint: ${HARBOR_S3_ENDPOINT}
    secure: true
    v4auth: true
  redirect:
    disable: true
YML
    fi
    if [ "$EXT_DB" = true ]; then cat <<YML
external_database:
  harbor:
    host: ${HARBOR_DB_HOST}
    port: ${HARBOR_DB_PORT:-5432}
    db_name: ${HARBOR_DB_NAME:-registry}
    username: ${HARBOR_DB_USER}
    password: ${HARBOR_DB_EXT_PASSWORD}
    ssl_mode: require
    max_idle_conns: 2
    max_open_conns: 0
external_redis:
  host: ${HARBOR_REDIS_ADDR}
  password: ${HARBOR_REDIS_PASSWORD:-}
  registry_db_index: 1
  jobservice_db_index: 2
  trivy_db_index: 5
  idle_timeout_seconds: 30
YML
    fi
}

render_caddyfile() {
    cat <<CADDY
${DOMAIN} {
    request_body {
        max_size 0
    }
    reverse_proxy 127.0.0.1:${HTTP_PORT}
}
CADDY
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log "Docker + Compose plugin present."; return 0
    fi
    run sh -c 'curl -fsSL https://get.docker.com | sh'
    run systemctl enable --now docker
}

install_standalone() {
    check_root; check_inputs
    ADMIN_PW="${HARBOR_ADMIN_PASSWORD:-$(gen_secret)}"; DB_PW="${HARBOR_DB_PASSWORD:-$(gen_secret)}"
    ensure_docker
    run mkdir -p "$INSTALL_ROOT" "$DATA_DIR"
    local tgz="harbor-offline-installer-${HARBOR_VERSION}.tgz"
    if [ ! -d "$INSTALL_ROOT/harbor" ]; then
        log "Downloading official offline installer ${HARBOR_VERSION} ..."
        run curl -fSL -o "$INSTALL_ROOT/$tgz" "$RELEASE_URL/$tgz"
        run curl -fSL -o "$INSTALL_ROOT/md5sum" "$RELEASE_URL/md5sum"
        if [ "$DRY" = false ]; then
            (cd "$INSTALL_ROOT" && grep " $tgz\$" md5sum | md5sum -c -) || error_exit "Checksum mismatch for $tgz"
        fi
        run tar -xzf "$INSTALL_ROOT/$tgz" -C "$INSTALL_ROOT"
    fi
    if [ "$DRY" = true ]; then
        log "[DRY-RUN] harbor.yml would be:"; render_harbor_yml | sed -E 's/(password|secretkey|accesskey): .*/\1: ***/'
    else
        render_harbor_yml > "$INSTALL_ROOT/harbor/harbor.yml"; chmod 600 "$INSTALL_ROOT/harbor/harbor.yml"
    fi
    log "Running install.sh --with-trivy ..."
    run bash -c "cd '$INSTALL_ROOT/harbor' && ./install.sh --with-trivy"
    if [ "$DRY" = false ]; then render_caddyfile > "$INSTALL_ROOT/Caddyfile"; fi
    run docker run -d --name harbor-caddy --restart unless-stopped --network host \
        -v "$INSTALL_ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" -v harbor_caddy_data:/data "$CADDY_IMAGE"
    log "Harbor is starting at https://${DOMAIN} (user: admin)."
    [ -n "${HARBOR_ADMIN_PASSWORD:-}" ] || log "Generated admin password (shown once): ${ADMIN_PW}"
    log "Next: create projects, robot accounts for CI, enable scan-on-push and tag retention."
}

# ---------- kubernetes ----------
render_values() {
    cat <<VAL
expose:
  type: ingress
  tls:
    enabled: true
    certSource: $([ -n "${HARBOR_TLS_SECRET:-}" ] && echo secret || echo auto)
    secret:
      secretName: "${HARBOR_TLS_SECRET:-}"
  ingress:
    hosts:
      core: ${DOMAIN}
externalURL: https://${DOMAIN}
existingSecretAdminPassword: harbor-admin
existingSecretAdminPasswordKey: HARBOR_ADMIN_PASSWORD
persistence:
  enabled: true
  resourcePolicy: "keep"
  imageChartStorage:
VAL
    if [ "$WITH_S3" = true ]; then cat <<VAL
    disableredirect: true
    type: s3
    s3:
      existingSecret: harbor-s3
      region: ${HARBOR_S3_REGION:-us-east-1}
      bucket: ${HARBOR_S3_BUCKET}
      regionendpoint: ${HARBOR_S3_ENDPOINT}
      secure: true
      v4auth: true
VAL
    else cat <<VAL
    type: filesystem
VAL
    fi
    cat <<VAL
trivy:
  enabled: true
VAL
    if [ "$EXT_DB" = true ]; then cat <<VAL
database:
  type: external
  external:
    host: "${HARBOR_DB_HOST}"
    port: "${HARBOR_DB_PORT:-5432}"
    username: "${HARBOR_DB_USER}"
    coreDatabase: "${HARBOR_DB_NAME:-registry}"
    existingSecret: harbor-db
    sslmode: "require"
redis:
  type: external
  external:
    addr: "${HARBOR_REDIS_ADDR}"
VAL
    fi
}

install_k8s() {
    check_inputs
    command -v kubectl >/dev/null || [ "$DRY" = true ] || error_exit "kubectl not found."
    command -v helm >/dev/null || [ "$DRY" = true ] || error_exit "helm not found."
    local pw="${HARBOR_ADMIN_PASSWORD:-$(gen_secret)}"
    run kubectl create namespace "$NS" --dry-run=client -o yaml | { [ "$DRY" = true ] && cat >/dev/null || kubectl apply -f -; }
    if [ "$DRY" = false ]; then
        kubectl -n "$NS" create secret generic harbor-admin --from-literal=HARBOR_ADMIN_PASSWORD="$pw" \
            --dry-run=client -o yaml | kubectl apply -f -
        if [ "$WITH_S3" = true ]; then
            kubectl -n "$NS" create secret generic harbor-s3 \
                --from-literal=REGISTRY_STORAGE_S3_ACCESSKEY="$HARBOR_S3_ACCESS_KEY" \
                --from-literal=REGISTRY_STORAGE_S3_SECRETKEY="$HARBOR_S3_SECRET_KEY" \
                --dry-run=client -o yaml | kubectl apply -f -
        fi
        if [ "$EXT_DB" = true ]; then
            kubectl -n "$NS" create secret generic harbor-db --from-literal=password="$HARBOR_DB_EXT_PASSWORD" \
                --dry-run=client -o yaml | kubectl apply -f -
        fi
    fi
    local vf; vf="$(mktemp)"; render_values > "$vf"
    [ "$DRY" = true ] && { log "[DRY-RUN] values.yaml:"; cat "$vf"; }
    run helm repo add harbor "$HELM_REPO"
    run helm repo update harbor
    run helm upgrade --install harbor harbor/harbor --version "$HARBOR_CHART_VERSION" \
        --namespace "$NS" -f "$vf" --wait --timeout 15m
    rm -f "$vf"
    log "Harbor chart ${HARBOR_CHART_VERSION} (app ${HARBOR_VERSION}) released to namespace ${NS}: https://${DOMAIN}"
    [ -n "${HARBOR_ADMIN_PASSWORD:-}" ] || log "Generated admin password (shown once): ${pw}"
}

uninstall() {
    if [ "$MODE" = k8s ]; then
        run helm uninstall harbor --namespace "$NS"
        log "PVCs kept (resourcePolicy: keep). Delete them manually only after backup."
        return
    fi
    check_root
    run docker rm -f harbor-caddy || true
    if [ -d "$INSTALL_ROOT/harbor" ]; then run bash -c "cd '$INSTALL_ROOT/harbor' && docker compose down"; fi
    run tar -czf "$INSTALL_ROOT-config-$(date +%Y%m%d%H%M%S).tgz" -C "$INSTALL_ROOT" .
    log "Stopped Harbor. Data kept in ${DATA_DIR}."
}

main() {
    if [ "$UNINSTALL" = true ]; then uninstall; exit 0; fi
    [ -n "$MODE" ] || { show_help; error_exit "Choose --standalone or --k8s."; }
    [ -n "$DOMAIN" ] || error_exit "--domain is required."
    validate_domain "$DOMAIN"
    case "$MODE" in standalone) install_standalone ;; k8s) install_k8s ;; esac
}
main
