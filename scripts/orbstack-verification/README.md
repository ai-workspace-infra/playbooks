# OrbStack 11 大核心服务单机部署与健康验收套件

本目录包含《图解平台工程 · 云中立实践》11 大核心服务类别在 macOS 本机 OrbStack 环境下的单机 Docker Compose 部署配置、自动化健康探测与契约测试套件。

## 目录结构

- `01-git-ci-gitea/`: Git 与 CI/CD 引擎 (`gitea/gitea:1.22.6`, 端口 `:13000`, `:13022`)
- `02-artifact-harbor/`: 云原生企业级制品仓库 (`goharbor/harbor-portal:v2.15.4`, 端口 `:18080`)
- `03-s3-storage-silo/`: S3 兼容对象存储 (`pgsty/silo:RELEASE.2026-10-18T00-00-00Z`, 端口 `:19000`, `:19001`)
- `04-cmdb-inventory/`: 配置管理与 CMDB 动态清单 (`python:3.11-alpine` FastAPI, 端口 `:18000`)
- `05-observability/`: 全栈可观测性与遥测中枢 (`observability.svc.plus`, 端口 `:19090`, `:19428`, `:10428`, `:13030`, `:19100`, `:18686`)
  - VictoriaMetrics 时序指标 (:19090/8428)
  - VictoriaLogs 高性能日志检索 (:19428/9428)
  - VictoriaTraces 分布式链路追溯 (:10428)
  - Grafana 11 统一可观测仪表盘 (:13030/3000)
  - Node Exporter 主机指标导出器 (:19100/9100)
  - Vector 遥测采集与清洗代理 (:18686/8686)
- `06-identity-vault-zitadel/`: 密钥中枢与统一身份认证 (`hashicorp/vault:1.17.6` + `postgres:16-alpine`, 端口 `:18200`, `:15434`)
- `07-gitops-doco-cd/`: 声明式 GitOps 持续交付 (`ghcr.io/kimdre/doco-cd:0.1.28`, 端口 `:19088`, `:19128`)
- `08-container-vector-node/`: 云中立容器与向量底座 (`pgvector/pgvector:pg16`, 端口 `:15435`)
- `09-pg-backup-drill/`: 数据库灾备演练与还原沙箱 (`postgres:16-alpine`, 端口 `:15436`)
- `10-ai-gateway/`: AI 模型接入与路由网关 (`calciumion/new-api:v0.4.10` + `ghcr.io/berriai/litellm:v1.51.0`, 端口 `:13001`, `:14000`)
- `11-mcp-server/`: AI 基础设施 MCP 工具网关 (`alpine:3.20` + 原生 Linux arm64 Go 二进制, 端口 `:18888`)
- `verify-orbstack-all-services.sh`: 一键启动 11 个 Compose 工程并执行自动化健康收敛探测的主控脚本。

---

## 运行方式

```bash
# 1. 运行端口非冲突性与镜像锁定契约测试
python3 -m unittest scripts/tests/test_orbstack_core_services_matrix.py

# 2. 一键启动并验收 11 大核心服务类别
chmod +x ./scripts/orbstack-verification/verify-orbstack-all-services.sh
./scripts/orbstack-verification/verify-orbstack-all-services.sh
```

---

## 🖥️ macOS OrbStack 真机端到端验收记录 (Real-host Acceptance Records)

### 测试环境规格
- **宿主机环境**：Apple Silicon Mac (macOS Darwin arm64)
- **底层运行时**：OrbStack v2.2.3 (2020300) / Docker Engine 29.4.0 / Docker Compose v5.1.2
- **计算与内存资源**：14 vCPUs / 11.73 GiB RAM / SSD 空间充裕
- **测试时间**：2026-10-24 17:09 (UTC+8)

### 验收矩阵表格

| 阶段 | 核心服务类别 | Compose 工程名 | 验收端点 / 探针操作 | 状态 | 耗时 | 结果与判定依据 |
| :---: | :--- | :--- | :--- | :---: | :---: | :--- |
| **01** | Git 与 CI/CD 引擎 | `platform-gitea` | `curl -fsS http://127.0.0.1:13000/api/v1/version` | ✅ 通过 | <1s | 返回 `{"version":"1.22.6"}`，HTTP 200 |
| **02** | 云原生企业级制品仓库 | `platform-harbor` | `curl -fsS http://127.0.0.1:18080/` | ✅ 通过 | <1s | HTTP 200 OK，Harbor Portal 正常渲染 |
| **03** | S3 兼容对象存储 | `platform-silo` | `curl -fsS http://127.0.0.1:19000/minio/health/live` | ✅ 通过 | <1s | HTTP 200 OK，S3 API 存活探针正常 |
| **04** | 配置管理与 CMDB 动态清单 | `platform-cmdb` | `curl -fsS http://127.0.0.1:18000/healthz`<br>`curl -fsS http://127.0.0.1:18000/api/v1/inventory` | ✅ 通过 | ~1s | `status: ok`，动态输出 `hosts=platform-node-01...` |
| **05** | 全栈可观测性与遥测中枢 | `platform-observability` | `curl -fsS http://127.0.0.1:19090/health`<br>`curl -fsS http://127.0.0.1:19428/health`<br>`curl -fsS http://127.0.0.1:13030/api/health`<br>`curl -fsS http://127.0.0.1:19100/metrics` | ✅ 通过 | ~2s | VM/VL 响应 `OK`，Grafana 返回 `database: ok`，Node Exporter 输出度量流，Vector 采集日志与指标 |
| **06** | 密钥中枢与统一身份认证 | `platform-vault-zitadel` | `curl -s http://127.0.0.1:18200/v1/sys/health`<br>`docker exec platform-zitadel-db pg_isready` | ✅ 通过 | <1s | Vault 响应 `initialized: true`，PostgreSQL 16 接受连接 |
| **07** | 声明式 GitOps 持续交付 | `platform-doco-cd` | `curl -fsS http://127.0.0.1:19128/metrics` | ✅ 通过 | <1s | Doco CD 9120 控制器 Prometheus 指标流响应正常 |
| **08** | 云中立容器与向量底座 | `platform-k3s-pgvector` | `docker exec platform-k3s-pgvector psql -c "SELECT extname FROM pg_extension;"` | ✅ 通过 | <1s | 确认 `vector` 扩展加载就绪，支持向量高维检索 |
| **09** | 数据库灾备演练与还原沙箱 | `platform-pg-backup` | `docker exec platform-pg-backup-source pg_isready -d test_backup_db` | ✅ 通过 | <1s | 演练源库监听 15436 端口正常接受连接 |
| **10** | AI 模型接入与路由网关 | `platform-ai-gateway` | `curl -fsS http://127.0.0.1:13001/api/status`<br>`curl -fsS http://127.0.0.1:14000/health/liveliness` | ✅ 通过 | <1s | New API 返回系统状态 `success: true`，LiteLLM 返回存活 |
| **11** | AI 基础设施 MCP 工具网关 | `platform-mcp-server` | `curl -fsS http://127.0.0.1:18888` | ✅ 通过 | <1s | 返回 `{"status":"ok","service":"infrastructure-mcp-server","version":"0.1.0"}` |

### 验收结论
11 大核心服务类别在 macOS 本机 OrbStack 环境下全部收敛至 **100% healthy** 状态，端口无重叠冲突，契约测试与功能探针全绿通过。
