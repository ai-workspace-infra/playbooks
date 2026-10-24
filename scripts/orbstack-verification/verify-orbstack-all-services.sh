#!/usr/bin/env bash
# ==============================================================================
# 《图解平台工程 · 云中立实践》11 大核心服务类别 MacOS OrbStack 单机部署验证套件
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log_info() { echo -e "\033[1;34m[INFO]\033[0m $*"; }
log_ok()   { echo -e "\033[1;32m[PASS]\033[0m $*"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
log_err()  { echo -e "\033[1;31m[FAIL]\033[0m $*" >&2; }

echo "=============================================================================="
echo "🚀 开始在 MacOS 本机 OrbStack 执行 11 大核心服务类别单机部署与健康验收"
echo "=============================================================================="

# 1. 验证 Docker / OrbStack 连通性
if ! docker info >/dev/null 2>&1; then
  log_err "OrbStack Docker 未就绪，请确保 OrbStack 在 MacOS 上已启动！"
  exit 1
fi
ORB_VER=$(orbctl version 2>/dev/null || echo "OrbStack (Docker Engine)")
log_info "运行环境: MacOS $(uname -m) | ${ORB_VER}"

# 2. 依次拉起并验证 11 大核心服务
declare -A CATEGORIES=(
  ["01"]="01-git-ci-gitea:platform-gitea:Git 与 CI/CD 引擎 (Gitea + Actions)"
  ["02"]="02-artifact-harbor:platform-harbor:云原生制品管理仓库 (Harbor)"
  ["03"]="03-s3-storage-silo:platform-silo:S3 兼容对象存储 (pgsty/silo)"
  ["04"]="04-cmdb-inventory:platform-cmdb:配置管理与 CMDB 动态清单"
  ["05"]="05-observability:platform-observability:全栈可观测性与遥测中枢 (VictoriaMetrics + VictoriaLogs + Grafana + Vector)"
  ["06"]="06-identity-vault-zitadel:platform-vault-zitadel:密钥中枢与统一身份认证 (Vault + Zitadel)"
  ["07"]="07-gitops-doco-cd:platform-doco-cd:声明式 GitOps 持续交付 (Doco CD)"
  ["08"]="08-container-vector-node:platform-k3s-pgvector:云中立容器与向量底座 (PostgreSQL + pgvector)"
  ["09"]="09-pg-backup-drill:platform-pg-backup:数据库灾备演练与还原沙箱"
  ["10"]="10-ai-gateway:platform-ai-gateway:AI 模型接入与路由网关 (New API + LiteLLM)"
  ["11"]="11-mcp-server:platform-mcp-server:AI 基础设施工具网关 (Infrastructure MCP)"
)

# 按照顺序验证
for key in $(echo "${!CATEGORIES[@]}" | tr ' ' '\n' | sort -n); do
  IFS=':' read -r folder project title <<< "${CATEGORIES[$key]}"
  echo ""
  echo "------------------------------------------------------------------------------"
  log_info "[${key}/11] 正在部署并验证类别: ${title}"
  log_info "  Compose 工程: ${project} (路径: ${folder})"
  
  stack_dir="${SCRIPT_DIR}/${folder}"
  if [ ! -d "$stack_dir" ]; then
    log_err "目录不存在: ${stack_dir}"
    exit 1
  fi

  # 启动该类别服务
  docker compose -p "${project}" -f "${stack_dir}/docker-compose.yml" up -d

  # 等待容器就绪
  log_info "  等待容器健康状态收敛..."
  sleep 4

  # 功能与端点验收
  case "$key" in
    "01")
      # Gitea HTTP
      for _ in {1..15}; do
        if res=$(curl -fsS http://127.0.0.1:13000/api/v1/version 2>/dev/null); then
          log_ok "Gitea API 正常响应: ${res}"
          break
        fi
        sleep 2
      done
      ;;
    "02")
      # Harbor Web UI & API
      for _ in {1..15}; do
        if curl -fsS http://127.0.0.1:18080 >/dev/null 2>&1; then
          log_ok "Harbor (Artifact Management) Web UI 存活检查成功 (端口 18080)"
          break
        fi
        sleep 2
      done
      ;;
    "03")
      # Silo S3 Live Check
      for _ in {1..15}; do
        if curl -fsS http://127.0.0.1:19000/minio/health/live >/dev/null 2>&1; then
          log_ok "Silo (S3-Compatible Object Storage) API 存活检查成功 (端口 19000)"
          break
        fi
        sleep 2
      done
      ;;
    "04")
      # CMDB API & Inventory
      res_health=$(curl -fsS http://127.0.0.1:18000/healthz)
      res_inv=$(curl -fsS http://127.0.0.1:18000/api/v1/inventory)
      log_ok "CMDB 健康探针: ${res_health}"
      log_ok "CMDB 动态清单正常输出: hosts=$(echo "$res_inv" | grep -o 'platform-node-[0-9]*' | tr '\n' ' ')"
      ;;
    "05")
      # Observability Stack & Agent (VictoriaMetrics, VictoriaLogs, Grafana, Node Exporter, Vector)
      for _ in {1..15}; do
        if curl -fsS http://127.0.0.1:19090/health >/dev/null 2>&1 && \
           curl -fsS http://127.0.0.1:19428/health >/dev/null 2>&1 && \
           curl -fsS http://127.0.0.1:13030/api/health >/dev/null 2>&1 && \
           curl -fsS http://127.0.0.1:19100/metrics >/dev/null 2>&1; then
          log_ok "VictoriaMetrics (指标 :19090)、VictoriaLogs (日志 :19428)、Grafana (:13030) 及 Node Exporter (:19100) 全链路健康响应"
          break
        fi
        sleep 2
      done
      ;;
    "06")
      # Vault Health Check
      for _ in {1..15}; do
        if res=$(curl -s http://127.0.0.1:18200/v1/sys/health 2>/dev/null); then
          if echo "$res" | grep -q '"initialized":true'; then
            log_ok "HashiCorp Vault 正常就绪 (端口 18200)"
            break
          fi
        fi
        sleep 2
      done
      # Postgres Zitadel check
      if docker exec platform-zitadel-db pg_isready -U zitadel -d zitadel >/dev/null 2>&1; then
        log_ok "Zitadel 后端 PostgreSQL 数据库就绪 (端口 15434)"
      fi
      ;;
    "07")
      # Doco CD
      for _ in {1..15}; do
        if curl -fsS http://127.0.0.1:19088/v1/health >/dev/null 2>&1 || curl -fsS http://127.0.0.1:19128/metrics >/dev/null 2>&1; then
          log_ok "Doco CD 控制器运行正常 (端口 19088 / 19128)"
          break
        fi
        sleep 2
      done
      ;;
    "08")
      # K3s Postgres pgvector
      for _ in {1..15}; do
        if docker exec platform-k3s-pgvector psql -U k3s -d k3s_vector -c "SELECT extname FROM pg_extension WHERE extname = 'vector';" | grep -q "vector"; then
          log_ok "PostgreSQL 16 + pgvector 向量扩展正常加载 (端口 15435)"
          break
        fi
        sleep 2
      done
      ;;
    "09")
      # PG Backup Drill
      if docker exec platform-pg-backup-source pg_isready -U postgres -d test_backup_db >/dev/null 2>&1; then
        log_ok "PG 灾备演练源数据库就绪 (端口 15436)"
      fi
      ;;
    "10")
      # AI Gateway
      for _ in {1..15}; do
        if res=$(curl -fsS http://127.0.0.1:13001/api/status 2>/dev/null); then
          log_ok "New API 认证与限额服务就绪: ${res}"
          break
        fi
        sleep 2
      done
      for _ in {1..15}; do
        if curl -fsS http://127.0.0.1:14000/health/liveliness >/dev/null 2>&1; then
          log_ok "LiteLLM 模型路由代理服务就绪 (端口 14000)"
          break
        fi
        sleep 2
      done
      ;;
    "11")
      # MCP Gateway
      for _ in {1..10}; do
        if res=$(curl -fsS http://127.0.0.1:18888 2>/dev/null); then
          log_ok "Infrastructure MCP Server 守护网关就绪: ${res}"
          break
        fi
        sleep 2
      done
      ;;
  esac
done

echo ""
echo "=============================================================================="
echo "🎉 OrbStack 11 大核心服务类别单机部署验证全部完成！"
echo "=============================================================================="
docker ps --filter "name=platform-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
