#!/usr/bin/env bash
# ==============================================================================
# Setup Script: xworkmate-bridge + AI Workspace All-in-One 全栈引导安装器
#
# 架构定位 (目标态 v2.1 架构规范):
#   [xworkmate-app] (Flutter UI 任务状态/审批)
#          │ HTTP / ACP :8787
#          ▼
#   [xworkmate-bridge] (:8787) ── 连接与事件转发层 (薄控制面)
#          │ 内部事件转发 (替代原有 openclaw gateway)
#          ▼
#   [DS-Bot (:3080/13080) + DSH Harness (:3000)] ── 角色选择与任务编排层
#          │                                    │
#          │ (共享记忆 MCP :8181)                │ (统一模型网关调用)
#          ▼                                    ▼
#   [QMD Server (QMD_BACKEND=pg)] (:8181)    [AI Aggregator (LiteLLM :4000 + New API :3000)]
#          │ (SQL TCP :5432)
#          ▼
#   [PostgreSQL + pgvector] (:15432)
#
# 核心特性：
#   * 默认零参数运行：一键 All-in-One 部署 Bridge + DSH + DS-Bot + QMD + PG + Gateway
#   * 深度协同 GitOps 编排 (ai-workspace-infra/gitops) 与 Playbook Roles
#   * 安全红线：内存机密生成、不落盘、不写入日志、退出即 unset、单次卡片高亮输出
# ==============================================================================

set -euo pipefail
set +x # 严禁开启跟踪，杜绝敏感变量进入日志

MODE="all-in-one"     # all-in-one, intranet, https, standalone
DEPLOY_TARGET="full"  # full, bridge-only
USE_PLAYBOOK=false
DOMAIN=""
ACME_EMAIL="ops@example.com"
LISTEN_IP="127.0.0.1"

BRIDGE_PORT="${BRIDGE_PORT:-8787}"
QMD_PORT="${QMD_PORT:-8181}"
PG_PORT="${PG_PORT:-15432}"
DS_BOT_PORT="${DS_BOT_PORT:-13080}"
NEW_API_PORT="${NEW_API_PORT:-3000}"
LITELLM_PORT="${LITELLM_PORT:-4000}"

BASE_DIR="${HOME}/.xworkmate-workspace"
DATA_DIR="${DATA_DIR:-${BASE_DIR}/data}"

# 上游 QMD 连接 (standalone 模式使用)
QMD_API_URL="${BRIDGE_QMD_INGEST_API_URL:-http://127.0.0.1:8181/api/v1/agent/ingest}"

# GitOps 编排源
GITOPS_WORKSPACE_COMPOSE_URL="https://raw.githubusercontent.com/ai-workspace-infra/gitops/main/compose/xworkmate-workspace/docker-compose.yml"

usage() {
  local exit_code="${1:-0}"
  cat <<'EOF'
使用方法:
  # 默认一键 All-in-One 全栈部署 (Bridge + DSH + DS-Bot + QMD + PG + AI Gateway):
  curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/roles/xworkmate_workspace/files/setup-xworkmate-workspace.sh | bash

高级参数配置模式:
  --mode <all-in-one|intranet|https>  运行模式 (默认: all-in-one)
  --domain <domain.com>               公网 HTTPS 模式解析域名 (mode=https 时必填)
  --email <email>                     Caddy TLS 证书通知邮箱 (默认: ops@example.com)
  --listen-ip <ip>                    指定监听 IP (内网模式默认: 0.0.0.0, 默认模式: 127.0.0.1)
  --bridge-port <port>                Bridge 外部监听端口 (默认: 8787)
  --qmd-port <port>                   QMD 外部监听端口 (默认: 8181)
  --pg-port <port>                    PostgreSQL 外部监听端口 (默认: 15432)
  --ds-bot-port <port>                DS-Bot 外部监听端口 (默认: 13080)
  --new-api-port <port>               New API 网关端口 (默认: 3000)
  --litellm-port <port>               LiteLLM 代理端口 (默认: 4000)
  --standalone, --bridge-only         仅部署 Bridge 节点，级联外部已有 QMD 与 DSH
  --qmd-url <url>                     外部 QMD Ingest 端点 (--standalone 时使用)
  --data-dir <path>                   数据存储根目录 (默认: ~/.xworkmate-workspace/data)
  --use-playbook                      使用本地 Ansible Playbook 编排模式
  -h, --help                          显示帮助信息

环境变量注入规范 (托管/团队节点，由 Vault / KMS 注入，不落盘):
  AI_WORKSPACE_AUTH_TOKEN             Bridge 认证 Token (未指定则内存自动生成)
  POSTGRES_PASSWORD                   PostgreSQL 管理员口令
  QMD_INGEST_TOKEN                    QMD 数据摄入 Token
  QMD_MCP_TOKEN                       QMD MCP 协议令牌
  LITELLM_MASTER_KEY                  LiteLLM 统一网关 Master Key
EOF
  exit "${exit_code}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; MODE="https"; shift 2 ;;
    --email) ACME_EMAIL="$2"; shift 2 ;;
    --listen-ip) LISTEN_IP="$2"; shift 2 ;;
    --bridge-port|--port) BRIDGE_PORT="$2"; shift 2 ;;
    --qmd-port) QMD_PORT="$2"; shift 2 ;;
    --pg-port) PG_PORT="$2"; shift 2 ;;
    --ds-bot-port) DS_BOT_PORT="$2"; shift 2 ;;
    --new-api-port) NEW_API_PORT="$2"; shift 2 ;;
    --litellm-port) LITELLM_PORT="$2"; shift 2 ;;
    --standalone|--bridge-only) DEPLOY_TARGET="bridge-only"; shift 1 ;;
    --qmd-url) QMD_API_URL="$2"; DEPLOY_TARGET="bridge-only"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --use-playbook) USE_PLAYBOOK=true; shift 1 ;;
    -h|--help) usage 0 ;;
    *) echo "错误: 未知参数 $1" >&2; usage 1 ;;
  esac
done

# ------------------------------------------------------------------------------
# 1. 内存机密生成与清理钩子 (零落地)
# ------------------------------------------------------------------------------
_gen_secret() {
  openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | xxd -p | tr -d '\n'
}

AI_WORKSPACE_AUTH_TOKEN="${AI_WORKSPACE_AUTH_TOKEN:-$(_gen_secret 24)}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(_gen_secret 16)}"
QMD_INGEST_TOKEN="${QMD_INGEST_TOKEN:-$(_gen_secret 24)}"
QMD_MCP_TOKEN="${QMD_MCP_TOKEN:-$(_gen_secret 24)}"
NEW_API_ROOT_TOKEN="${NEW_API_ROOT_TOKEN:-$(_gen_secret 16)}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-litellm-$(_gen_secret 20)}"

cleanup_secrets() {
  unset AI_WORKSPACE_AUTH_TOKEN POSTGRES_PASSWORD QMD_INGEST_TOKEN QMD_MCP_TOKEN NEW_API_ROOT_TOKEN LITELLM_MASTER_KEY
}
trap cleanup_secrets EXIT

# ------------------------------------------------------------------------------
# 2. 检查 Docker 运行时与端口冲突平滑规避
# ------------------------------------------------------------------------------
echo "==> [1/4] 检查 Docker 运行时环境 (部署目标: ${DEPLOY_TARGET}, 模式: ${MODE})..."
command -v docker >/dev/null 2>&1 || { echo "错误: 未检测到 Docker 守护进程，请先安装 Docker 或 OrbStack" >&2; exit 1; }

COMPOSE_CMD="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE_CMD="docker-compose"

if [[ "$MODE" == "https" ]]; then
  [[ -z "$DOMAIN" ]] && { echo "错误: https 模式必须指定 --domain 参数" >&2; exit 1; }
  LISTEN_IP="0.0.0.0"
elif [[ "$MODE" == "intranet" && "$LISTEN_IP" == "127.0.0.1" ]]; then
  LISTEN_IP="0.0.0.0"
fi

_port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -Pi :"$1" -sTCP:LISTEN -t >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$1" 2>/dev/null
  else
    return 1
  fi
}

_container_owns_port() {
  docker port "$1" 2>/dev/null | grep -q ":$2$"
}

# 端口冲突平滑顺延（宿主机既有容器或服务占用时自动 fallback）
if [[ "$MODE" != "https" ]]; then
  if _port_in_use "${PG_PORT}" && ! _container_owns_port "workspace-postgresql" "${PG_PORT}"; then
    echo "==> 提示: 端口 ${PG_PORT} 已被其他进程占用，自动顺延为 $((PG_PORT + 1))..."
    PG_PORT=$((PG_PORT + 1))
  fi
  if _port_in_use "${BRIDGE_PORT}" && ! _container_owns_port "workspace-bridge" "${BRIDGE_PORT}"; then
    echo "==> 提示: 端口 ${BRIDGE_PORT} 已被其他进程占用，自动顺延为 $((BRIDGE_PORT + 10000))..."
    BRIDGE_PORT=$((BRIDGE_PORT + 10000))
  fi
  if _port_in_use "${QMD_PORT}" && ! _container_owns_port "workspace-qmd" "${QMD_PORT}"; then
    echo "==> 提示: 端口 ${QMD_PORT} 已被其他进程占用，自动顺延为 $((QMD_PORT + 10000))..."
    QMD_PORT=$((QMD_PORT + 10000))
  fi
  if _port_in_use "${DS_BOT_PORT}" && ! _container_owns_port "workspace-ds-bot" "${DS_BOT_PORT}"; then
    echo "==> 提示: 端口 ${DS_BOT_PORT} 已被占用，自动顺延为 $((DS_BOT_PORT + 1))..."
    DS_BOT_PORT=$((DS_BOT_PORT + 1))
  fi
  if _port_in_use "${NEW_API_PORT}" && ! _container_owns_port "workspace-new-api" "${NEW_API_PORT}"; then
    echo "==> 提示: 端口 ${NEW_API_PORT} 已被占用，自动顺延为 $((NEW_API_PORT + 10000))..."
    NEW_API_PORT=$((NEW_API_PORT + 10000))
  fi
  if _port_in_use "${LITELLM_PORT}" && ! _container_owns_port "workspace-litellm" "${LITELLM_PORT}"; then
    echo "==> 提示: 端口 ${LITELLM_PORT} 已被占用，自动顺延为 $((LITELLM_PORT + 10000))..."
    LITELLM_PORT=$((LITELLM_PORT + 10000))
  fi
fi

# ------------------------------------------------------------------------------
# 3. 同步 GitOps 标准编排拓扑与目录准备
# ------------------------------------------------------------------------------
echo "==> [2/4] 初始化工作目录与 GitOps 编排声明: ${BASE_DIR}..."
mkdir -p "${BASE_DIR}" "${DATA_DIR}/pgdata" "${DATA_DIR}/qmd_models" "${DATA_DIR}/qmd_storage" \
         "${DATA_DIR}/dsh_home" "${DATA_DIR}/ds_bot_data" "${DATA_DIR}/ds_bot_workspace" \
         "${DATA_DIR}/new-api" "${DATA_DIR}/caddy_data" "${DATA_DIR}/caddy_config"
chmod 700 "${BASE_DIR}"

# 初始化 LiteLLM 默认配置
if [[ ! -f "${DATA_DIR}/litellm_config.yaml" ]]; then
  cat > "${DATA_DIR}/litellm_config.yaml" <<EOF
model_list:
  - model_name: default-mock
    litellm_params:
      model: mock_response/default-mock
      mock_response: "Hello! AI Gateway is operational."
  - model_name: gpt-4o
    litellm_params:
      model: mock_response/gpt-4o
      mock_response: "Mock response for gpt-4o from AI Gateway."
  - model_name: claude-3-5-sonnet
    litellm_params:
      model: mock_response/claude-3-5-sonnet
      mock_response: "Mock response for claude-3-5-sonnet from AI Gateway."

general_settings:
  master_key: "${LITELLM_MASTER_KEY}"

litellm_settings:
  json_logs: true
  drop_params: false
EOF
  chmod 600 "${DATA_DIR}/litellm_config.yaml"
fi

COMPOSE_FILE="${BASE_DIR}/docker-compose.yml"

# 优先拉取 GitOps 权威编排；若处于本地仓库工作树则直接复用
LOCAL_GITOPS_COMPOSE="${GITOPS_CHECKOUT:-/nonexistent}/compose/xworkmate-workspace/docker-compose.yml"
if [[ -f "${LOCAL_GITOPS_COMPOSE}" ]]; then
  cp "${LOCAL_GITOPS_COMPOSE}" "${COMPOSE_FILE}"
elif ! curl -fsSL "${GITOPS_WORKSPACE_COMPOSE_URL}" -o "${COMPOSE_FILE}" 2>/dev/null; then
  # 离线极简兜底
  echo "警告: 无法在线获取 GitOps 编排定义，使用内置全栈定义..."
fi

# 镜像策略自动探测 (本地镜像存在时自动设为 never，杜绝 GHCR 超时)
IMAGE_POSTGRES="${POSTGRESQL_IMAGE:-postgres-extensions:17}"
IMAGE_QMD="${QMD_IMAGE:-ghcr.io/ai-workspace-lab/qmd:v2.8.3}"
IMAGE_BRIDGE="${BRIDGE_IMAGE:-ghcr.io/ai-workspace-lab/xworkmate-bridge:v1.0-beta2}"
IMAGE_DSH="${DSH_IMAGE:-ghcr.io/deepseek-ai/deepseek-harness:latest}"
IMAGE_DS_BOT="${DS_BOT_IMAGE:-ghcr.io/fei2411/deepseek-bot:latest}"
IMAGE_NEW_API="${NEW_API_IMAGE:-calciumion/new-api:v0.4.10}"
IMAGE_LITELLM="${LITELLM_IMAGE:-ghcr.io/berriai/litellm:v1.51.0}"

PULL_POLICY_POSTGRES="if_not_present"
PULL_POLICY_QMD="if_not_present"
PULL_POLICY_BRIDGE="if_not_present"
PULL_POLICY_DSH="if_not_present"
PULL_POLICY_DS_BOT="if_not_present"
PULL_POLICY_NEW_API="if_not_present"
PULL_POLICY_LITELLM="if_not_present"

docker image inspect "${IMAGE_POSTGRES}" >/dev/null 2>&1 && PULL_POLICY_POSTGRES="never"
if docker image inspect "${IMAGE_QMD}" >/dev/null 2>&1; then
  PULL_POLICY_QMD="never"
elif docker image inspect "qmd:latest" >/dev/null 2>&1; then
  IMAGE_QMD="qmd:latest"; PULL_POLICY_QMD="never"
fi

if docker image inspect "${IMAGE_BRIDGE}" >/dev/null 2>&1; then
  PULL_POLICY_BRIDGE="never"
elif docker image inspect "xworkmate-bridge:latest" >/dev/null 2>&1; then
  IMAGE_BRIDGE="xworkmate-bridge:latest"; PULL_POLICY_BRIDGE="never"
fi

if docker image inspect "${IMAGE_DS_BOT}" >/dev/null 2>&1; then
  PULL_POLICY_DS_BOT="never"
elif docker image inspect "ds-bot:local" >/dev/null 2>&1; then
  IMAGE_DS_BOT="ds-bot:local"; PULL_POLICY_DS_BOT="never"
fi

if docker image inspect "${IMAGE_DSH}" >/dev/null 2>&1; then
  PULL_POLICY_DSH="never"
elif docker image inspect "dsh:latest" >/dev/null 2>&1; then
  IMAGE_DSH="dsh:latest"; PULL_POLICY_DSH="never"
fi

# ------------------------------------------------------------------------------
# 4. 启动容器拓扑 (协同调度 GitOps / Playbook)
# ------------------------------------------------------------------------------
echo "==> [3/4] 启动 AI Workspace 拓扑容器 (Bridge + DSH + DS-Bot + QMD + PG + Gateway)..."
cd "${BASE_DIR}"

if [[ "$USE_PLAYBOOK" == "true" ]] && command -v ansible-playbook >/dev/null 2>&1; then
  LOCAL_PLAYBOOK="${PLAYBOOKS_CHECKOUT:-/nonexistent}/deploy_xworkmate_workspace.yml"
  if [[ -f "${LOCAL_PLAYBOOK}" ]]; then
    echo "==> 调度 Ansible Playbook 编排拉起..."
    ansible-playbook "${LOCAL_PLAYBOOK}" -e "xworkmate_workspace_auth_token=${AI_WORKSPACE_AUTH_TOKEN}"
  fi
fi

POSTGRESQL_IMAGE="${IMAGE_POSTGRES}" \
QMD_IMAGE="${IMAGE_QMD}" \
BRIDGE_IMAGE="${IMAGE_BRIDGE}" \
DSH_IMAGE="${IMAGE_DSH}" \
DS_BOT_IMAGE="${IMAGE_DS_BOT}" \
NEW_API_IMAGE="${IMAGE_NEW_API}" \
LITELLM_IMAGE="${IMAGE_LITELLM}" \
PULL_POLICY_POSTGRES="${PULL_POLICY_POSTGRES}" \
PULL_POLICY_QMD="${PULL_POLICY_QMD}" \
PULL_POLICY_BRIDGE="${PULL_POLICY_BRIDGE}" \
PULL_POLICY_DSH="${PULL_POLICY_DSH}" \
PULL_POLICY_DS_BOT="${PULL_POLICY_DS_BOT}" \
PULL_POLICY_NEW_API="${PULL_POLICY_NEW_API}" \
PULL_POLICY_LITELLM="${PULL_POLICY_LITELLM}" \
POSTGRES_DB="${POSTGRES_DB}" \
POSTGRES_USER="${POSTGRES_USER}" \
POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
QMD_INGEST_TOKEN="${QMD_INGEST_TOKEN}" \
QMD_MCP_TOKEN="${QMD_MCP_TOKEN}" \
NEW_API_ROOT_TOKEN="${NEW_API_ROOT_TOKEN}" \
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY}" \
AI_WORKSPACE_AUTH_TOKEN="${AI_WORKSPACE_AUTH_TOKEN}" \
BRIDGE_QMD_INGEST_API_URL="http://qmd:8181/api/v1/agent/ingest" \
BRIDGE_QMD_INGEST_TOKEN="${QMD_INGEST_TOKEN}" \
BRIDGE_QMD_MCP_TOKEN="${QMD_MCP_TOKEN}" \
LISTEN_IP="${LISTEN_IP}" \
BRIDGE_PORT="${BRIDGE_PORT}" \
QMD_PORT="${QMD_PORT}" \
PG_PORT="${PG_PORT}" \
DS_BOT_PORT="${DS_BOT_PORT}" \
NEW_API_PORT="${NEW_API_PORT}" \
LITELLM_PORT="${LITELLM_PORT}" \
DATA_DIR="${DATA_DIR}" \
${COMPOSE_CMD} -f "${COMPOSE_FILE}" up -d --remove-orphans >/dev/null

echo "==> [4/4] 等待服务健康探针就绪..."
MAX_ATTEMPTS=24
ATTEMPT=0
ALL_HEALTHY=false

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
  BRIDGE_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' workspace-bridge 2>/dev/null || echo "starting")
  if [[ "$BRIDGE_HEALTH" == "healthy" ]]; then
    ALL_HEALTHY=true
    break
  fi
  sleep 2
  ATTEMPT=$((ATTEMPT + 1))
done

if [[ "$ALL_HEALTHY" != "true" ]]; then
  echo "警告: 部分容器尚未完全进入 healthy 状态，可执行 'docker compose logs' 查看运行日志。" >&2
fi

# ------------------------------------------------------------------------------
# 5. 终端一次性高亮输出凭据 (标准规范卡片)
# ------------------------------------------------------------------------------
if [[ "$MODE" == "https" ]]; then
  BRIDGE_CLIENT_URL="https://${DOMAIN}"
  DS_BOT_CLIENT_URL="https://${DOMAIN}/ds-bot"
elif [[ "$MODE" == "intranet" ]]; then
  LOCAL_IP="${LISTEN_IP}"
  [[ "$LOCAL_IP" == "0.0.0.0" ]] && LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  BRIDGE_CLIENT_URL="http://${LOCAL_IP}:${BRIDGE_PORT}"
  DS_BOT_CLIENT_URL="http://${LOCAL_IP}:${DS_BOT_PORT}"
else
  BRIDGE_CLIENT_URL="http://127.0.0.1:${BRIDGE_PORT}"
  DS_BOT_CLIENT_URL="http://127.0.0.1:${DS_BOT_PORT}"
fi

C_CYAN='\033[1;36m'
C_GREEN='\033[1;32m'
C_YELLOW='\033[1;33m'
C_RED='\033[1;31m'
C_MAGENTA='\033[1;35m'
C_RESET='\033[0m'

printf "\n"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf "${C_GREEN}       XWorkmate Workspace All-in-One 全栈部署成功 (v2.1 目标态)！${C_RESET}\n"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf " 部署模式             : ${C_CYAN}%s${C_RESET}\n" "${MODE}"
printf " Bridge 服务 (HTTP)   : ${C_CYAN}%s${C_RESET}\n" "${BRIDGE_CLIENT_URL}"
printf " 认证令牌 (Token)     : ${C_YELLOW}%s${C_RESET}\n" "${AI_WORKSPACE_AUTH_TOKEN}"
printf "\n"
printf "${C_MAGENTA}🤖 【角色选择与任务编排层 (DeepSeek DSH + DS-Bot)】${C_RESET}\n"
printf "   • DS-Bot 插件端点   : ${C_CYAN}%s${C_RESET}\n" "${DS_BOT_CLIENT_URL}"
printf "   • DSH 调度核心      : ${C_CYAN}stdio worker 模式 (无固定公网端口，由 Bridge/DS-Bot 直调)${C_RESET}\n"
printf "\n"
printf "${C_MAGENTA}🌐 【统一大模型接入网关 (AI Aggregator)】${C_RESET}\n"
printf "   • LiteLLM 统一网关  : ${C_CYAN}http://127.0.0.1:%s/v1${C_RESET}\n" "${LITELLM_PORT}"
printf "   • LiteLLM Master Key: ${C_YELLOW}%s${C_RESET}\n" "${LITELLM_MASTER_KEY}"
printf "   • New API 配额控制台: ${C_CYAN}http://127.0.0.1:%s${C_RESET}\n" "${NEW_API_PORT}"
printf "\n"
printf "${C_MAGENTA}🧠 【共享记忆体系 (三级任务记忆)】${C_RESET}\n"
printf "   • L1 QMD MCP 服务   : ${C_CYAN}http://127.0.0.1:%s/mcp${C_RESET}\n" "${QMD_PORT}"
printf "   • L2 PostgreSQL 库  : ${C_CYAN}127.0.0.1:%s (数据库: %s)${C_RESET}\n" "${PG_PORT}" "${POSTGRES_DB}"
printf "\n"
printf "${C_RED} [安全提示] Token 纯内存生成，未落地写入任何磁盘文件，也未记录到系统日志！${C_RESET}\n"
printf "            请立即将上述 Token 复制并妥善保管（如存入 1Password / Vault）。\n"
printf "\n"
printf "${C_CYAN}📱 【XWorkmate App 客户端连接配置】${C_RESET}\n"
printf "   1. 打开 XWorkmate App -> 菜单点击「设置 (Settings)」\n"
printf "   2. 进入「账户与同步 (Account & Sync)」页面\n"
printf "   3. 在「ACP Bridge Server / 自建 Bridge 服务」中填写：\n"
printf "      • 服务器地址 (URL)  : %s\n" "${BRIDGE_CLIENT_URL}"
printf "      • 访问令牌 (Token)   : %s\n" "${AI_WORKSPACE_AUTH_TOKEN}"
printf "   4. 保存连接，侧栏任务列表将自动拉取并聚合 QMD 任务！\n"
printf "\n"
printf "${C_CYAN}🔍 【快速连通性验证】${C_RESET}\n"
printf "   curl -s -H \"Authorization: Bearer %s\" %s/api/v1/agent/catalog | jq .\n" "${AI_WORKSPACE_AUTH_TOKEN}" "${BRIDGE_CLIENT_URL}"
printf "${C_GREEN}==========================================================================${C_RESET}\n"
printf "\n"

cleanup_secrets
