#!/usr/bin/env bash
# setup-observability.sh - One-line installer for Full-Stack Observability (Server & Agent)
# Targets: Debian 12+, Ubuntu 22.04+
# Reference: https://github.com/ai-workspace-infra/observability & observability.svc.plus
set -euo pipefail
umask 022

readonly VICTORIA_METRICS_IMAGE="victoriametrics/victoria-metrics:v1.101.0"
readonly VICTORIA_LOGS_IMAGE="victoriametrics/victoria-logs:v1.52.0"
readonly VICTORIA_TRACES_IMAGE="victoriametrics/victoria-traces:v0.1.0"
readonly OTEL_COLLECTOR_IMAGE="otel/opentelemetry-collector-contrib:0.114.0"
readonly GRAFANA_IMAGE="grafana/grafana:11.3.0"
readonly NODE_EXPORTER_IMAGE="prom/node-exporter:v1.8.2"
readonly VECTOR_IMAGE="timberio/vector:0.41.1-alpine"
readonly CADDY_IMAGE="caddy:2.8.4-alpine"

readonly SERVER_INSTALL_ROOT="/opt/observability-server"
readonly AGENT_INSTALL_ROOT="/opt/observability-agent"
readonly SERVER_DATA_DIR="/data/observability"

MODE=""
DOMAIN="${OBSERVABILITY_DOMAIN:-observability.svc.plus}"
GRAFANA_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-}"
VECTOR_USER="${VECTOR_AUTH_USER:-}"
VECTOR_PASSWORD="${VECTOR_AUTH_PASSWORD:-}"
SERVER_ENDPOINT="${OBSERVABILITY_ENDPOINT:-http://127.0.0.1:19090}"
NODE_NAME="${OBSERVABILITY_NODE_NAME:-$(hostname -s 2>/dev/null || echo "node-01")}"
DRY_RUN=false
UNINSTALL=false
GENERATED_AUTH=false

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

gen_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24
    else
        head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24
    fi
}

check_root() {
    if [ "$DRY_RUN" = false ] && [ "$(id -u)" -ne 0 ]; then
        error_exit "This script must be run as root (or with sudo)."
    fi
}

show_help() {
    cat <<'HELP'
Usage: setup-observability.sh (--server | --agent) [options]
       setup-observability.sh --uninstall [--server | --agent] [--dry-run]
       setup-observability.sh --help

Installs the Cloud-Neutral Full-Stack Observability Platform (observability.svc.plus):
  - Server: VictoriaMetrics (Metrics), VictoriaLogs (Logs), VictoriaTraces (Traces),
            OpenTelemetry Collector, Grafana 11 Dashboards & Caddy HTTPS Gateway.
  - Agent:  Node Exporter (Host Metrics) + Vector Agent (Metrics & Log Pipeline).

Modes:
  --server              Deploy Observability Server Stack (Metrics, Logs, Traces, Grafana, Caddy).
  --agent               Deploy Observability Agent on host (Node Exporter + Vector).

Options:
  --domain <fqdn>       Server public domain (default: observability.svc.plus). Or $OBSERVABILITY_DOMAIN.
  --endpoint <url>      (Agent mode) Center server ingestion endpoint (default: http://127.0.0.1:19090).
  --node-name <name>    (Agent mode) Reporting node hostname. Or $OBSERVABILITY_NODE_NAME.
  --dry-run             Render compose configurations and print planned actions without executing.
  --uninstall           Stop containers, save configuration archive, and retain persistent data.
  --help, -h            Show this help message.

Environment Variables:
  OBSERVABILITY_DOMAIN          Public domain for Grafana and Caddy.
  GRAFANA_ADMIN_PASSWORD        Initial admin password for Grafana (generated if omitted).
  VECTOR_AUTH_USER              Caddy HTTP Basic Auth username for agent telemetry ingest.
  VECTOR_AUTH_PASSWORD          Caddy HTTP Basic Auth password for agent telemetry ingest.
  VAULT_ADDR / VAULT_TOKEN      (Optional) Read credentials from Vault kv/data/CICD/observability.
  OBSERVABILITY_ENDPOINT        Center telemetry ingest endpoint for agent.

Printed Book Commands:
  # Standalone Observability Server Demo
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-observability.sh | \
    bash -s -- --server --domain observability.example.com

  # Standalone Observability Agent on Worker Node
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-observability.sh | \
    bash -s -- --agent --endpoint https://observability.example.com
HELP
    exit 0
}

resolve_credentials() {
    if [ -n "$VECTOR_USER" ] && [ -n "$VECTOR_PASSWORD" ]; then
        return 0
    fi

    # Try Vault if configured
    if [ -n "${VAULT_ADDR:-}" ] && [ -n "${VAULT_TOKEN:-}" ]; then
        local secret_path="${VAULT_OBSERVABILITY_SECRET_PATH:-kv/data/CICD/observability}"
        log "Attempting to resolve monitoring credentials from Vault ($secret_path) ..."
        if command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
            local resp
            resp="$(curl -fsSL -H "X-Vault-Token: ${VAULT_TOKEN}" "${VAULT_ADDR}/v1/${secret_path}" 2>/dev/null || true)"
            if [ -n "$resp" ]; then
                local u p
                u="$(echo "$resp" | jq -r '.data.data.user // empty' 2>/dev/null || true)"
                p="$(echo "$resp" | jq -r '.data.data.password // empty' 2>/dev/null || true)"
                if [ -n "$u" ] && [ -n "$p" ]; then
                    VECTOR_USER="$u"
                    VECTOR_PASSWORD="$p"
                    log "Successfully loaded telemetry credentials from Vault."
                    return 0
                fi
            fi
        fi
    fi

    # If in server mode, generate credentials and output once
    if [ "$MODE" = "server" ]; then
        VECTOR_USER="obs_$(gen_secret | cut -c1-8)"
        VECTOR_PASSWORD="$(gen_secret)"
        GENERATED_AUTH=true
    fi
}

render_server_compose() {
    local pw="${GRAFANA_PASSWORD:-admin}"
    cat <<EOF
version: '3.8'

services:
  victoria-metrics:
    image: ${VICTORIA_METRICS_IMAGE}
    container_name: obs-victoria-metrics
    restart: always
    command:
      - '-storageDataPath=/vmetrics_data'
      - '-retentionPeriod=30d'
      - '-promscrape.config=/etc/prometheus/prometheus.yml'
    volumes:
      - ${SERVER_DATA_DIR}/vmetrics:/vmetrics_data
      - ${SERVER_INSTALL_ROOT}/config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "127.0.0.1:19090:8428"

  victoria-logs:
    image: ${VICTORIA_LOGS_IMAGE}
    container_name: obs-victoria-logs
    restart: always
    command:
      - '-storageDataPath=/vlogs_data'
      - '-retentionPeriod=1y'
    volumes:
      - ${SERVER_DATA_DIR}/vlogs:/vlogs_data
    ports:
      - "127.0.0.1:19428:9428"

  victoria-traces:
    image: ${VICTORIA_TRACES_IMAGE}
    container_name: obs-victoria-traces
    restart: always
    command:
      - '-storageDataPath=/vtraces_data'
    volumes:
      - ${SERVER_DATA_DIR}/vtraces:/vtraces_data
    ports:
      - "127.0.0.1:10428:10428"
      - "127.0.0.1:14318:4318"
      - "127.0.0.1:14317:4317"

  otel-collector:
    image: ${OTEL_COLLECTOR_IMAGE}
    container_name: obs-otel-collector
    restart: always
    command: ["--config=/etc/otelcol-contrib/config.yaml"]
    volumes:
      - ${SERVER_INSTALL_ROOT}/config/otel-collector.yml:/etc/otelcol-contrib/config.yaml:ro
    ports:
      - "127.0.0.1:4319:4318"
    depends_on:
      - victoria-metrics
      - victoria-traces
      - victoria-logs

  grafana:
    image: ${GRAFANA_IMAGE}
    container_name: obs-grafana
    restart: always
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=${pw}
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_SERVER_ROOT_URL=https://${DOMAIN}/grafana/
      - GF_SERVER_SERVE_FROM_SUB_PATH=true
    volumes:
      - ${SERVER_DATA_DIR}/grafana:/var/lib/grafana
      - ${SERVER_INSTALL_ROOT}/config/grafana-datasources.yaml:/etc/grafana/provisioning/datasources/datasources.yaml:ro
    ports:
      - "127.0.0.1:13030:3000"
    depends_on:
      - victoria-metrics

  caddy:
    image: ${CADDY_IMAGE}
    container_name: obs-caddy
    restart: always
    volumes:
      - ${SERVER_INSTALL_ROOT}/config/Caddyfile:/etc/caddy/Caddyfile:ro
      - obs_caddy_data:/data
    network_mode: host
    depends_on:
      - grafana
      - victoria-metrics

volumes:
  obs_caddy_data:
EOF
}

render_server_configs() {
    mkdir -p "${SERVER_INSTALL_ROOT}/config"

    cat <<'EOF' > "${SERVER_INSTALL_ROOT}/config/prometheus.yml"
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: 'victoriametrics'
    static_configs:
      - targets: ['127.0.0.1:8428']
EOF

    cat <<'EOF' > "${SERVER_INSTALL_ROOT}/config/otel-collector.yml"
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
  debug:
    verbosity: basic
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
EOF

    cat <<'EOF' > "${SERVER_INSTALL_ROOT}/config/grafana-datasources.yaml"
apiVersion: 1
datasources:
  - name: VictoriaMetrics
    type: prometheus
    access: proxy
    url: http://127.0.0.1:19090
    isDefault: true
  - name: VictoriaLogs
    type: victoriametrics-logs-datasource
    access: proxy
    url: http://127.0.0.1:19428
EOF

    local tls_line=""
    if [[ "${DOMAIN}" =~ \.(example\.com|local|internal|test)$ ]]; then
        tls_line="    tls internal"
    fi

    cat <<EOF > "${SERVER_INSTALL_ROOT}/config/Caddyfile"
${DOMAIN} {
${tls_line}
    # Grafana Dashboard UI
    handle_path /grafana/* {
        reverse_proxy 127.0.0.1:13030
    }

    # Basic Auth Ingestion for Remote Agents
    handle /ingest/* {
        basic_auth {
            ${VECTOR_USER} ${VECTOR_PASSWORD}
        }
        reverse_proxy 127.0.0.1:19090
    }

    # Fallback to Grafana
    handle {
        reverse_proxy 127.0.0.1:13030
    }
}
EOF
}

render_agent_compose() {
    cat <<EOF
version: '3.8'

services:
  node-exporter:
    image: ${NODE_EXPORTER_IMAGE}
    container_name: obs-node-exporter
    restart: always
    command:
      - '--path.rootfs=/host'
    volumes:
      - '/:/host:ro,rslave'
    ports:
      - "127.0.0.1:19100:9100"

  vector:
    image: ${VECTOR_IMAGE}
    container_name: obs-vector-agent
    restart: always
    command: ["--config", "/etc/vector/vector.toml"]
    volumes:
      - ${AGENT_INSTALL_ROOT}/config/vector.toml:/etc/vector/vector.toml:ro
      - /var/log:/var/log:ro
    ports:
      - "127.0.0.1:18686:8686"
    depends_on:
      - node-exporter
EOF
}

render_agent_configs() {
    mkdir -p "${AGENT_INSTALL_ROOT}/config"

    cat <<EOF > "${AGENT_INSTALL_ROOT}/config/vector.toml"
[sources.node_metrics]
type = "prometheus_scrape"
endpoints = ["http://127.0.0.1:19100/metrics"]
scrape_interval_secs = 15

[transforms.add_node_tag]
type = "remap"
inputs = ["node_metrics"]
source = '''
.tags.node = "${NODE_NAME}"
'''

[sinks.center_metrics]
type = "prometheus_remote_write"
inputs = ["add_node_tag"]
endpoint = "${SERVER_ENDPOINT}/api/v1/write"
EOF
}

install_server() {
    check_root
    GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-$(gen_secret)}"
    resolve_credentials

    if [ "$DRY_RUN" = true ]; then
        log "[DRY-RUN] Observability Server docker-compose.yml:"
        render_server_compose | sed -E "s/(${GRAFANA_PASSWORD}|${VECTOR_PASSWORD})/********/g"
        log "[DRY-RUN] Configurations would be rendered into ${SERVER_INSTALL_ROOT}/config/"
        return 0
    fi

    log "Preparing installation directories in ${SERVER_INSTALL_ROOT} and ${SERVER_DATA_DIR} ..."
    mkdir -p "${SERVER_INSTALL_ROOT}" "${SERVER_DATA_DIR}/vmetrics" "${SERVER_DATA_DIR}/vlogs" "${SERVER_DATA_DIR}/vtraces" "${SERVER_DATA_DIR}/grafana"
    render_server_configs
    render_server_compose > "${SERVER_INSTALL_ROOT}/docker-compose.yml"

    log "Starting Observability Server Stack (VictoriaMetrics, Logs, Traces, Otel, Grafana, Caddy) ..."
    (cd "${SERVER_INSTALL_ROOT}" && docker compose up -d)

    log "Waiting for service readiness ..."
    local ready=false
    for _ in {1..30}; do
        if curl -fsSL "http://127.0.0.1:13030/api/health" >/dev/null 2>&1 && \
           curl -fsSL "http://127.0.0.1:19090/health" >/dev/null 2>&1; then
            ready=true; break
        fi
        sleep 2
    done

    if [ "$ready" = true ]; then
        log "Observability Server is online and healthy!"
    else
        warn "Services are starting; check 'docker compose ps' in ${SERVER_INSTALL_ROOT}."
    fi

    log "Grafana URL:     https://${DOMAIN}/grafana/ (User: admin, Pass: ${GRAFANA_PASSWORD})"
    log "VictoriaMetrics: http://127.0.0.1:19090"
    log "VictoriaLogs:    http://127.0.0.1:19428"
    log "VictoriaTraces:  http://127.0.0.1:10428"
    if [ "$GENERATED_AUTH" = true ]; then
        echo "=============================================================================="
        echo "🔑 Generated Agent Ingest Credentials (shown once; save securely):"
        echo "   VECTOR_AUTH_USER=${VECTOR_USER}"
        echo "   VECTOR_AUTH_PASSWORD=${VECTOR_PASSWORD}"
        echo "=============================================================================="
    fi
}

install_agent() {
    check_root
    resolve_credentials

    if [ "$DRY_RUN" = true ]; then
        log "[DRY-RUN] Observability Agent docker-compose.yml:"
        render_agent_compose
        log "[DRY-RUN] Vector pipeline config would point to ${SERVER_ENDPOINT}"
        return 0
    fi

    log "Preparing agent installation directory in ${AGENT_INSTALL_ROOT} ..."
    mkdir -p "${AGENT_INSTALL_ROOT}"
    render_agent_configs
    render_agent_compose > "${AGENT_INSTALL_ROOT}/docker-compose.yml"

    log "Starting Observability Agent Stack (Node Exporter + Vector) ..."
    (cd "${AGENT_INSTALL_ROOT}" && docker compose up -d)

    log "Waiting for agent readiness ..."
    local ready=false
    for _ in {1..20}; do
        if curl -fsSL "http://127.0.0.1:19100/metrics" >/dev/null 2>&1; then
            ready=true; break
        fi
        sleep 1
    done

    if [ "$ready" = true ]; then
        log "Observability Agent is reporting metrics locally on :19100 and streaming to center!"
    else
        warn "Agent services starting; check 'docker compose ps' in ${AGENT_INSTALL_ROOT}."
    fi
}

uninstall_observability() {
    check_root
    if [ "$MODE" = "agent" ] || [ -d "${AGENT_INSTALL_ROOT}" ]; then
        log "Stopping Observability Agent ..."
        if [ -f "${AGENT_INSTALL_ROOT}/docker-compose.yml" ]; then
            (cd "${AGENT_INSTALL_ROOT}" && docker compose down 2>/dev/null || true)
        fi
        tar -czf "/opt/observability-agent-backup-$(date +%Y%m%d%H%M%S).tgz" -C "${AGENT_INSTALL_ROOT}" . 2>/dev/null || true
        log "Agent stopped. Configuration backed up."
    fi

    if [ "$MODE" = "server" ] || [ -d "${SERVER_INSTALL_ROOT}" ]; then
        log "Stopping Observability Server Stack ..."
        if [ -f "${SERVER_INSTALL_ROOT}/docker-compose.yml" ]; then
            (cd "${SERVER_INSTALL_ROOT}" && docker compose down 2>/dev/null || true)
        fi
        tar -czf "/opt/observability-server-backup-$(date +%Y%m%d%H%M%S).tgz" -C "${SERVER_INSTALL_ROOT}" . 2>/dev/null || true
        log "Server stopped. Data retained in ${SERVER_DATA_DIR}."
    fi
}

# Parse Command-Line Arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --server)
            MODE="server"
            shift
            ;;
        --agent)
            MODE="agent"
            shift
            ;;
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --endpoint)
            SERVER_ENDPOINT="$2"
            shift 2
            ;;
        --node-name)
            NODE_NAME="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            error_exit "Unknown option: $1 (see --help)"
            ;;
    esac
done

if [ "$UNINSTALL" = true ]; then
    uninstall_observability
    exit 0
fi

if [ -z "$MODE" ]; then
    error_exit "Please specify either --server or --agent mode (see --help)."
fi

if [ "$MODE" = "server" ]; then
    install_server
elif [ "$MODE" = "agent" ]; then
    install_agent
fi
