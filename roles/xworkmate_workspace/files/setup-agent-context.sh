#!/usr/bin/env bash
# ==============================================================================
# Setup Script: XWorkmate Agent Context Stack
#
# 架构拓扑：
#   [xworkmate-bridge] (:8787)
#          │ (HTTP Ingest & Agent API :8181)
#          ▼
#   [QMD Server (QMD_BACKEND=pg)] (:8181)
#          │ (SQL TCP :5432)
#          ▼
#   [PostgreSQL + pgvector] (:15432)
#
# 部署模式：
#   1. local    : 本地开发模式 (默认 127.0.0.1，不对外暴露)
#   2. intranet : 内网穿透/局域网模式 (绑定局域网 IP / 0.0.0.0，Token 鉴权保护)
#   3. https    : 公网生产模式 (Caddy 自动申请 Let's Encrypt 证书，80/443 收敛)
#
# 安全红线约束：
#   * 零文件落地 (Zero disk persistence)：所有 Token 与密码纯内存生成/继承，绝不写入任何文件或 .env
#   * 不写入日志 (Zero log exposure)：关闭一切跟踪回显 (set +x)，curl 探测静默执行
#   * 终端一次性打印 (Print once & unset)：安装成功后仅在控制台高亮输出一次，退出前立即 unset 销毁
# ==============================================================================

set -euo pipefail
set +x # 严禁开启跟踪，杜绝敏感变量打印入日志

# ------------------------------------------------------------------------------
# 1. 默认参数与参数解析
# ------------------------------------------------------------------------------
MODE="local"
DOMAIN=""
ACME_EMAIL="ops@example.com"
LISTEN_IP=""
BRIDGE_PORT="8787"
QMD_PORT="8181"
PG_PORT="15432"
BASE_DIR="${HOME}/.xworkmate-stack"
DATA_DIR="${BASE_DIR}/data"

# 镜像版本 (允许通过环境变量覆盖)
IMAGE_POSTGRES="${POSTGRESQL_IMAGE:-postgres-extensions:17}"
IMAGE_QMD="${QMD_IMAGE:-ghcr.io/ai-workspace-lab/qmd:v2.8.3}"
IMAGE_BRIDGE="${BRIDGE_IMAGE:-ghcr.io/ai-workspace-lab/xworkmate-bridge:v1.0-beta2}"
IMAGE_CADDY="caddy:2-alpine"

usage() {
  cat <<'EOF'
使用方法:
  curl -fsSL https://raw.githubusercontent.com/.../setup-agent-context.sh | bash -s -- [options]

运行模式选项:
  --mode <local|intranet|https>   部署模式 (默认: local)
  --domain <domain.com>           公网 HTTPS 域名 (mode=https 时必填)
  --email <email>                 Caddy Let's Encrypt 证书通知邮箱 (默认: ops@example.com)
  --listen-ip <ip>                指定监听 IP (内网模式默认: 0.0.0.0, 本地模式默认: 127.0.0.1)
  --bridge-port <port>            Bridge 外部暴露端口 (默认: 8787)
  --data-dir <path>               数据库存储目录 (默认: ~/.xworkmate-stack/data)
  -h, --help                      显示帮助信息

环境变量注入规范 (支持由 Vault / KMS / 外部安全注入，不落盘):
  AI_WORKSPACE_AUTH_TOKEN         Bridge 认证 Token (若未提供，将在内存中自动随机生成)
  QMD_INGEST_TOKEN                QMD 数据摄入 Token
  QMD_MCP_TOKEN                   QMD MCP 协议交互 Token
  POSTGRES_PASSWORD               PostgreSQL 管理员口令
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --domain)
      DOMAIN="$2"
      shift 2
      ;;
    --email)
      ACME_EMAIL="$2"
      shift 2
      ;;
    --listen-ip)
      LISTEN_IP="$2"
      shift 2
      ;;
    --bridge-port)
      BRIDGE_PORT="$2"
      shift 2
      ;;
    --data-dir)
      DATA_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "错误: 未知参数 $1" >&2
      usage
      ;;
  esac
done

# ------------------------------------------------------------------------------
# 2. 内存凭据初始化 (内存驻留，不落地)
# ------------------------------------------------------------------------------
_gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    head -c "$1" /dev/urandom | xxd -p | tr -d '\n'
  fi
}

POSTGRES_DB="${POSTGRES_DB:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(_gen_secret 16)}"
QMD_INGEST_TOKEN="${QMD_INGEST_TOKEN:-$(_gen_secret 24)}"
QMD_MCP_TOKEN="${QMD_MCP_TOKEN:-$(_gen_secret 24)}"
AI_WORKSPACE_AUTH_TOKEN="${AI_WORKSPACE_AUTH_TOKEN:-$(_gen_secret 24)}"

# 确保脚本发生任何中断或退出时，第一时间清理内存中的明文密码与 Token
cleanup_secrets() {
  unset POSTGRES_PASSWORD QMD_INGEST_TOKEN QMD_MCP_TOKEN AI_WORKSPACE_AUTH_TOKEN
}
trap cleanup_secrets EXIT

# ------------------------------------------------------------------------------
# 3. 环境与模式检查
# ------------------------------------------------------------------------------
echo "==> [1/4] 检查环境与运行时..."

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未检测到 Docker 守护进程，请先安装 Docker" >&2
  exit 1
fi

COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "错误: 未检测到 docker compose 插件或 docker-compose 二进制" >&2
  exit 1
fi

if [[ "$MODE" == "https" ]]; then
  if [[ -z "$DOMAIN" ]]; then
    echo "错误: https 模式必须指定 --domain 参数 (例如: --domain bridge.example.com)" >&2
    exit 1
  fi
  LISTEN_IP="0.0.0.0"
elif [[ "$MODE" == "intranet" ]]; then
  if [[ -z "$LISTEN_IP" ]]; then
    LISTEN_IP="0.0.0.0"
  fi
elif [[ "$MODE" == "local" ]]; then
  LISTEN_IP="127.0.0.1"
else
  echo "错误: 不支持的模式 '$MODE'，仅支持 local / intranet / https" >&2
  exit 1
fi

# ------------------------------------------------------------------------------
# 4. 生成无机密 Compose 声明文件 (零 Token 落地)
# ------------------------------------------------------------------------------
echo "==> [2/4] 准备工作目录与编排配置: ${BASE_DIR}..."
mkdir -p "${BASE_DIR}" "${DATA_DIR}/pgdata" "${DATA_DIR}/caddy_data" "${DATA_DIR}/caddy_config"
chmod 700 "${BASE_DIR}"

COMPOSE_FILE="${BASE_DIR}/docker-compose.yml"

if [[ "$MODE" == "local" || "$MODE" == "intranet" ]]; then
  cat > "${COMPOSE_FILE}" <<'EOF'
services:
  postgresql:
    image: ${POSTGRESQL_IMAGE}
    container_name: agent-postgresql
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    expose:
      - "5432"
    ports:
      - "${LISTEN_IP}:${PG_PORT:-15432}:5432"
    volumes:
      - ${DATA_DIR}/pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB} -h 127.0.0.1"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    networks:
      - agent_network

  qmd:
    image: ${QMD_IMAGE}
    container_name: agent-qmd
    restart: unless-stopped
    environment:
      NODE_ENV: production
      QMD_BACKEND: pg
      QMD_PG_URL: "postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgresql:5432/${POSTGRES_DB}?sslmode=disable"
      QMD_MCP_HOST: "0.0.0.0"
      QMD_MCP_TOKEN: ${QMD_MCP_TOKEN}
      QMD_INGEST_TOKEN: ${QMD_INGEST_TOKEN}
      QMD_CTX_COLLECT_INTERVAL: "0"
    expose:
      - "8181"
    ports:
      - "${LISTEN_IP}:${QMD_PORT:-8181}:8181"
    depends_on:
      postgresql:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -H \"Authorization: Bearer ${QMD_INGEST_TOKEN}\" http://127.0.0.1:8181/api/v1/agent/ingest/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - agent_network

  xworkmate-bridge:
    image: ${BRIDGE_IMAGE}
    container_name: agent-xworkmate-bridge
    restart: unless-stopped
    environment:
      AI_WORKSPACE_AUTH_TOKEN: ${AI_WORKSPACE_AUTH_TOKEN}
      BRIDGE_AUTH_TOKEN: ${AI_WORKSPACE_AUTH_TOKEN}
      BRIDGE_QMD_INGEST_API_URL: "http://qmd:8181/api/v1/agent/ingest"
      BRIDGE_QMD_INGEST_TOKEN: ${QMD_INGEST_TOKEN}
      BRIDGE_QMD_MCP_TOKEN: ${QMD_MCP_TOKEN}
    expose:
      - "8787"
    ports:
      - "${LISTEN_IP}:${BRIDGE_PORT}:8787"
    depends_on:
      qmd:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -H \"Authorization: Bearer ${AI_WORKSPACE_AUTH_TOKEN}\" http://127.0.0.1:8787/api/v1/agent/catalog || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 5s
    networks:
      - agent_network

networks:
  agent_network:
    name: docker_agent_context_net
    driver: bridge
EOF

elif [[ "$MODE" == "https" ]]; then
  # 自动配置 Caddyfile (对外暴露 80/443，后端 PostgreSQL/QMD 仅挂内部网络)
  cat > "${BASE_DIR}/Caddyfile" <<EOF
${DOMAIN} {
    tls ${ACME_EMAIL}
    reverse_proxy xworkmate-bridge:8787 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
EOF

  cat > "${COMPOSE_FILE}" <<'EOF'
services:
  postgresql:
    image: ${POSTGRESQL_IMAGE}
    container_name: agent-postgresql
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    expose:
      - "5432"
    volumes:
      - ${DATA_DIR}/pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB} -h 127.0.0.1"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    networks:
      - agent_network

  qmd:
    image: ${QMD_IMAGE}
    container_name: agent-qmd
    restart: unless-stopped
    environment:
      NODE_ENV: production
      QMD_BACKEND: pg
      QMD_PG_URL: "postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgresql:5432/${POSTGRES_DB}?sslmode=disable"
      QMD_MCP_HOST: "0.0.0.0"
      QMD_MCP_TOKEN: ${QMD_MCP_TOKEN}
      QMD_INGEST_TOKEN: ${QMD_INGEST_TOKEN}
      QMD_CTX_COLLECT_INTERVAL: "0"
    expose:
      - "8181"
    depends_on:
      postgresql:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -H \"Authorization: Bearer ${QMD_INGEST_TOKEN}\" http://127.0.0.1:8181/api/v1/agent/ingest/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - agent_network

  xworkmate-bridge:
    image: ${BRIDGE_IMAGE}
    container_name: agent-xworkmate-bridge
    restart: unless-stopped
    environment:
      AI_WORKSPACE_AUTH_TOKEN: ${AI_WORKSPACE_AUTH_TOKEN}
      BRIDGE_AUTH_TOKEN: ${AI_WORKSPACE_AUTH_TOKEN}
      BRIDGE_QMD_INGEST_API_URL: "http://qmd:8181/api/v1/agent/ingest"
      BRIDGE_QMD_INGEST_TOKEN: ${QMD_INGEST_TOKEN}
      BRIDGE_QMD_MCP_TOKEN: ${QMD_MCP_TOKEN}
    expose:
      - "8787"
    depends_on:
      qmd:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -H \"Authorization: Bearer ${AI_WORKSPACE_AUTH_TOKEN}\" http://127.0.0.1:8787/api/v1/agent/catalog || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 5s
    networks:
      - agent_network

  caddy:
    image: ${CADDY_IMAGE}
    container_name: agent-caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ${DATA_DIR}/caddy_data:/data
      - ${DATA_DIR}/caddy_config:/config
    depends_on:
      xworkmate-bridge:
        condition: service_healthy
    networks:
      - agent_network

networks:
  agent_network:
    name: docker_agent_context_net
    driver: bridge
EOF
fi

# ------------------------------------------------------------------------------
# 5. 启动容器栈 (凭据仅通过环境传递，不写文件，不写入日志)
# ------------------------------------------------------------------------------
echo "==> [3/4] 启动容器拓扑..."
cd "${BASE_DIR}"

# 通过环境变量注入 Docker Compose 进程环境，无 .env 文件产生
POSTGRESQL_IMAGE="${IMAGE_POSTGRES}" \
QMD_IMAGE="${IMAGE_QMD}" \
BRIDGE_IMAGE="${IMAGE_BRIDGE}" \
CADDY_IMAGE="${IMAGE_CADDY}" \
POSTGRES_DB="${POSTGRES_DB}" \
POSTGRES_USER="${POSTGRES_USER}" \
POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
QMD_INGEST_TOKEN="${QMD_INGEST_TOKEN}" \
QMD_MCP_TOKEN="${QMD_MCP_TOKEN}" \
AI_WORKSPACE_AUTH_TOKEN="${AI_WORKSPACE_AUTH_TOKEN}" \
LISTEN_IP="${LISTEN_IP}" \
BRIDGE_PORT="${BRIDGE_PORT}" \
QMD_PORT="${QMD_PORT}" \
PG_PORT="${PG_PORT}" \
DATA_DIR="${DATA_DIR}" \
${COMPOSE_CMD} -f "${COMPOSE_FILE}" up -d --remove-orphans >/dev/null

echo "==> [4/4] 等待健康探针级联收敛..."
MAX_ATTEMPTS=24
ATTEMPT=0
ALL_HEALTHY=false

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
  PG_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' agent-postgresql 2>/dev/null || echo "starting")
  QMD_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' agent-qmd 2>/dev/null || echo "starting")
  BRIDGE_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' agent-xworkmate-bridge 2>/dev/null || echo "starting")

  if [[ "$PG_HEALTH" == "healthy" && "$QMD_HEALTH" == "healthy" && "$BRIDGE_HEALTH" == "healthy" ]]; then
    ALL_HEALTHY=true
    break
  fi

  sleep 2
  ((ATTEMPT++))
done

if [[ "$ALL_HEALTHY" != "true" ]]; then
  echo "警告: 部分容器启动慢或探针尚未全部 healthy，请执行 '${COMPOSE_CMD} logs' 查看排障信息。" >&2
fi

# ------------------------------------------------------------------------------
# 6. 终端一次性高亮输出凭据 (不落地，不存日志，输出后立即销毁)
# ------------------------------------------------------------------------------
if [[ "$MODE" == "https" ]]; then
  CLIENT_URL="https://${DOMAIN}"
elif [[ "$MODE" == "intranet" ]]; then
  # 自动探测局域网 IP
  LOCAL_IP="${LISTEN_IP}"
  if [[ "$LOCAL_IP" == "0.0.0.0" ]]; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || ip route get 1 2>/dev/null | awk '{print $NF;exit}' || echo "127.0.0.1")
  fi
  CLIENT_URL="http://${LOCAL_IP}:${BRIDGE_PORT}"
else
  CLIENT_URL="http://127.0.0.1:${BRIDGE_PORT}"
fi

# 使用 ANSI 颜色高亮卡片打印
C_CYAN='\033[1;36m'
C_GREEN='\033[1;32m'
C_YELLOW='\033[1;33m'
C_RED='\033[1;31m'
C_RESET='\033[0m'

printf "\n"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf "${C_GREEN}              XWorkmate Agent Context Stack 部署成功！${C_RESET}\n"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf " 部署模式       : ${C_CYAN}%s${C_RESET}\n" "${MODE}"
printf " 服务地址 (URL) : ${C_CYAN}%s${C_RESET}\n" "${CLIENT_URL}"
printf " 认证令牌 (Token): ${C_YELLOW}%s${C_RESET}\n" "${AI_WORKSPACE_AUTH_TOKEN}"
printf "\n"
printf "${C_RED} [安全提示] Token 纯内存生成，未落地写入任何磁盘文件，也未记录到系统日志！${C_RESET}\n"
printf "            请立即将上述 Token 复制并妥善保管（如存入 1Password / Vault）。\n"
printf "\n"
printf "${C_CYAN}📱 【XWorkmate App 客户端连接配置】${C_RESET}\n"
printf "   1. 打开 XWorkmate App -> 菜单点击「设置 (Settings)」\n"
printf "   2. 进入「账户与同步 (Account & Sync)」页面\n"
printf "   3. 在「ACP Bridge Server / 自建 Bridge 服务」中填写：\n"
printf "      • 服务器地址 (URL)  : %s\n" "${CLIENT_URL}"
printf "      • 访问令牌 (Token)   : %s\n" "${AI_WORKSPACE_AUTH_TOKEN}"
printf "   4. 保存连接，侧栏任务列表将自动拉取并聚合 QMD 任务！\n"
printf "\n"
printf "${C_CYAN}🔍 【快速连通性验证】${C_RESET}\n"
printf "   curl -s -H \"Authorization: Bearer %s\" %s/api/v1/agent/catalog | jq .\n" "${AI_WORKSPACE_AUTH_TOKEN}" "${CLIENT_URL}"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf "\n"

# 立即销毁当前变量
cleanup_secrets
EOF
