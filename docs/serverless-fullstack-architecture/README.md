# 全栈无服务器与边缘架构可观测性体系指南 (Serverless & Edge Full-Stack Architecture Guide)

本文档系统性阐述 **Cloudflare Edge + Frontend Router + 5 SSR Workers + 3 Edge Gateway Workers + GCP Cloud Run + Supabase Cloud DB (PostgreSQL)** 的全栈无服务器架构，以及覆盖多环境（`sit` / `uat` / `prod`）的可观测性监控接入与认证方案。

---

## 1. 架构拓扑与流量流向 (Architecture Topology)

```
                                 Cloudflare Edge
                            (DNS, WAF, Ingress Traffic)
                                        |
                             Frontend Router (Worker)
                                        |
                +-----------------------+-----------------------+
                |                                               |
           SSR Workers (5 Workers)                     Edge Gateway (3 Workers)
      - public    - console                       - auth  (/api/auth/*)
      - content   - workspace                     - admin (/api/admin/*)
      - auth                                      - core  (/api/*)
                                                                |
                                                         Cloud Run (GCP)
                                                  - accounts
                                                  - content-service
                                                  - billing-service
                                                                |
                                                  ★ Supabase Cloud DB (PostgreSQL)
                                                     (重点关注 · Core Stateful DB)
```

### 1.1 云原生资产与控制台直达映射

| 架构层级 | 组件 / 标识 | 负责服务与职责 | 控制台直达 / 资源地址 |
|---|---|---|---|
| **01 · Edge & Ingress** | Cloudflare Zone (`onwalk.net` / `svc.plus`) | DNS 权威解析、WAF 防护、TLS 1.3 终止、Edge 缓存 | [Cloudflare Workers & Pages 控制台](https://dash.cloudflare.com/e71be5efb76a6c54f78f008da4404f00/workers-and-pages) (Account: `e71be5efb76a6c54f78f008da4404f00`) |
| **02 · Frontend Router** | `frontend-router-{env}` | Cloudflare Worker 路由中心，挂载于 `console-{env}` | [Frontend Router 视图](https://dash.cloudflare.com/e71be5efb76a6c54f78f008da4404f00/workers/services/view/frontend-router-uat/production) |
| **03 · 5 SSR Workers** | `public` / `content` / `auth` / `console` / `workspace` | 边缘动态 SSR 渲染分流 (`/*`, `/blogs*`, `/login*`, `/panel*`, `/ai-workspace*`) | [Workers 服务列表](https://dash.cloudflare.com/e71be5efb76a6c54f78f008da4404f00/workers-and-pages) |
| **04 · 静态资源 Pages** | `ai-workspace-portal-{env}.pages.dev` | 托管 Next.js / 前端静态资源 (`/_next/*`, `/static/*`, `/assets/*`) | Cloudflare Pages Project |
| **05 · 3 Gateway Workers** | `edge-gateway-auth` / `admin` / `core` | API Ingress 边界，挂载于 `accounts-{env}`，具备主备 failover 路由 | Cloudflare Worker Ingress |
| **06 · 弹性计算 (Cloud Run)** | `accounts` / `content-service` / `billing-service` | GCP 无服务器容器计算（区域：`asia-northeast1`） | [Google Cloud Run 控制台](https://console.cloud.google.com/run/services?project=xworktech) (Project: `xworktech`) |
| **07 · 核心数据库 (Supabase)** | PostgreSQL 15+ & Supavisor Pooler | 核心有状态业务数据库（**重点关注**） | [Supabase Project 控制台](https://supabase.com/dashboard/project/iqkxspmhcfqmhkbjdoms) (Project: `iqkxspmhcfqmhkbjdoms`) |

---

## 2. 多环境路由与域名契约 (Multi-Environment Matrix)

| 环境 | 控制平面 | 门户入口 (Console Domain) | 认证与网关入口 (Accounts Domain) | Pages 静态源站 | Cloud Run 区域 |
|---|---|---|---|---|---|
| **SIT** | Cloudflare DNS | `console-sit.onwalk.net` | `accounts-sit.onwalk.net` | `ai-workspace-portal-sit.pages.dev` | `asia-northeast1` |
| **UAT** | Cloudflare DNS | `console-uat.onwalk.net` | `accounts-uat.onwalk.net` | `ai-workspace-portal-uat.pages.dev` | `asia-northeast1` |
| **PROD** | Cloudflare DNS | `console.svc.plus` | `accounts.svc.plus` | `ai-workspace-portal-prod.pages.dev` | `asia-northeast1` |

---

## 3. 监控接入与认证细节 (Authentication & Ingestion TL;DR)

为了保证公网传输与多环境采集的安全性，所有外部组件向统一服务端（`https://observability.svc.plus`）推送数据时，均严格遵循 Vault 凭据隔离与认证通道规范。

```
[Cloudflare Edge]   --> (Logpush / Analytics API)    --> [Vector / Prometheus Exporter] --> [VictoriaMetrics / Logs]
[GCP Cloud Run]     --> (Cloud Monitoring / OTel)    --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Supabase DB]       --> (postgres_exporter / Drain)  --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Blackbox Exporter] --> (Synthetic HTTPS Probes)     --> [Prometheus Scraper]          --> [VictoriaMetrics]
```

### 3.1 链路一：Cloudflare Edge 接入与认证
```
[Cloudflare Edge] --(Logpush HTTP Sink)--> [Caddy /ingest/logs] --> [VictoriaLogs]
[Cloudflare API]  <--(GraphQL Pull)------ [CF Exporter] ---------> [VictoriaMetrics]
```
1. **Cloudflare Logpush (边缘日志直推)**：
   - **认证方式**：HTTP Header 注入 `Authorization: Basic <base64(vector_user:vector_pass)>` 或 Bearer Token。
   - **推送地址**：`https://observability.svc.plus/ingest/logs/insert/jsonline?_msg_field=message&_stream_fields=zone,worker,status`
   - **凭据来源**：Vault 路径 `kv/data/<env>/serverless/cloudflare` 中的 `CLOUDFLARE_LOGPUSH_AUTH_TOKEN`。
2. **Cloudflare Analytics & DNS 指标 (GraphQL API 轮询)**：
   - **认证方式**：API Token 认证（`Authorization: Bearer ${CLOUDFLARE_API_TOKEN}`）。
   - **权限范围**：`Analytics:Read`, `Zone:Read`, `Workers Analytics:Read`。
   - **凭据注入**：流水线自 Vault `kv/data/<env>/serverless/cloudflare` 读取 `CLOUDFLARE_API_TOKEN` 和 `CLOUDFLARE_ACCOUNT_ID`（`e71be5efb76a6c54f78f008da4404f00`）注入 Exporter。

---

### 3.2 链路二：GCP Cloud Run 计算指标与应用日志接入
```
[Cloud Run (xworktech)] --(OTel / Cloud Logging)--> [Vector Sink / OTel Collector] --> [VictoriaMetrics / Logs]
```
1. **GCP Cloud Monitoring / 指标拉取**：
   - **认证方式**：GitHub OIDC $\rightarrow$ Vault JWT $\rightarrow$ GCP Workload Identity 换取短期 GCP Access Token。
   - **Service Account 权限**：`roles/monitoring.viewer` 与 `roles/logging.viewer`。
   - **Vault 凭据路径**：
     - `kv/data/<env>/serverless/gcp/GCP_WORKLOAD_IDENTITY_PROVIDER`
     - `kv/data/<env>/serverless/gcp/GCP_SERVICE_ACCOUNT_EMAIL`
     - `kv/data/<env>/serverless/gcp/GCP_PROJECT_ID` (`xworktech`)
2. **Cloud Run 应用日志 (Log Router / Log Sink 导出)**：
   - **认证方式**：GCP Log Sink 通过 HTTPS Webhook 转发至 Vector 网关，携带签名密钥或 Basic Auth Header（`vector_agent:<password>`）。
   - **入库规范**：打标 `source_type="cloud_run"`, `env="${DEPLOY_ENV}"`, `service_name="accounts|content-service|billing-service"`。

---

### 3.3 链路三：Supabase Cloud DB (PostgreSQL) 深度监控接入
```
[Supabase DB (iqkxspmhcfqmhkbjdoms)] <--(TLS 5432/6543)-- [postgres_exporter] --> [VictoriaMetrics]
[Supabase Log Drain]                 --(HTTPS Webhook)--> [Vector Ingest]      --> [VictoriaLogs]
```
1. **数据库指标采集 (postgres_exporter)**：
   - **认证方式**：PostgreSQL SCRAM-SHA-256 / MD5 密码认证 + 强制 TLS (`sslmode=require`)。
   - **连接目标**：
     - **直连端口 (Direct URL)**：`postgres://postgres:<DB_PASS>@db.iqkxspmhcfqmhkbjdoms.supabase.co:5432/postgres?sslmode=require`
     - **连接池端口 (Pooler URL)**：`postgres://postgres.iqkxspmhcfqmhkbjdoms:<DB_PASS>@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require`
   - **Vault 凭据路径**：`kv/data/<env>/serverless/supabase` 中的 `DATABASE_DIRECT_URL` 与 `DATABASE_SESSION_POOLER_URL`。
   - **数据库最小只读角色权限**：
     ```sql
     GRANT pg_read_all_stats TO postgres_exporter;
     GRANT SELECT ON pg_stat_statements TO postgres_exporter;
     ```
2. **Supabase 慢查询与报错日志 (Log Drains)**：
   - **认证方式**：Supabase Webhook Destination 添加 Custom Headers（`Authorization: Basic <base64>` 或 `X-Vector-Auth: <token>`）。
   - **推送终点**：`https://observability.svc.plus/ingest/logs/insert/jsonline`，自动解析 `duration`、`statement`、`error_severity`。

---

### 3.4 链路四：Blackbox Exporter 终端可用性探测
```
[Blackbox Exporter] --(HTTPS GET / TLS Handshake)--> [Console / Accounts / Pages / API]
[Prometheus Scraper] <--(Scrape :9115/probe)--------- [VictoriaMetrics]
```
1. **黑盒探针执行**：
   - **认证策略**：对外网公网域名（如 `https://console-uat.onwalk.net`、`https://accounts-uat.onwalk.net`）发起标准无凭据探测（模拟真实用户终端接入），校验 HTTP 状态码（`200`/`404`/`302`）与 SSL 握手证书有效性。
   - **专用内部探针（可选）**：对受保护的管理接口（如 `/api/admin/health`）注入 Bearer Probe Token 探测。
2. **Prometheus Scraper 抓取**：
   - **认证方式**：通过本地回环 `127.0.0.1:9115` 或 Caddy `/blackbox/*` 经 Basic Auth 认证抓取指标，存入 VictoriaMetrics（`job="blackbox"`）。

---

## 4. Grafana 大盘概览 (Dashboard Specifications)

看板文件：`roles/docker/observability-server/files/serverless-edge-cloudrun-supabase-dashboard.json`  
UID：`serverless-fullstack-architecture`  
在线直达：[https://observability.svc.plus/grafana/d/serverless-fullstack-architecture/](https://observability.svc.plus/grafana/d/serverless-fullstack-architecture/)

### 8 大核心监控板块：
1. **01 · 全链路架构拓扑脉搏**：端到端状态流、SLA、边缘 QPS、SSR 耗时、Gateway 回退率、Cloud Run 活跃实例、Supabase 连接池饱和度及云控制台直达。
2. **02 · DNS 流量与边缘接入采集**：DNS 查询 QPS、HTTP 状态码分布 (2xx/3xx/4xx/5xx)、边缘缓存命中率与带宽。
3. **03 · 终端可用性与 SSL 探测**：端点状态表、SSL 证书倒计时趋势（<15d 警告, <7d 严重）、探针各阶段时延。
4. **04 · Cloudflare Frontend Router & 5 SSR Workers**：5 大 Worker 吞吐量 QPS、CPU 耗时、错误数与子请求。
5. **05 · Cloudflare Edge Gateway (3 Workers)**：边界 QPS (auth, admin, core)、主备路由回退分发比、JWT 鉴权耗时与 401/403 拦截率。
6. **06 · GCP Cloud Run 计算**：`accounts`、`content-service`、`billing-service` 请求 QPS 与 p95 响应耗时、活跃容器数、冷启动次数、CPU/内存利用率。
7. **07 · ★ Supabase Cloud DB (PostgreSQL)**：连接数与池负载、TPS、共享缓存命中率 (>99%)、查询耗时分位数 (p50/p95/p99)、死元组膨胀与 Autovacuum、存储与 WAL 速率。
8. **08 · 全栈统一日志流下钻**：组件错误与告警分布 (Bar Gauge)、Supabase DB 慢查询与报错表、Cloud Run & Edge 运行时日志表。

---

## 5. 部署与装载验证 (Verification)

1. **Ansible 自动装载**：已在 `roles/docker/observability-server/tasks/main.yml` 的 `Copy Grafana dashboards` 任务中注册 `serverless-edge-cloudrun-supabase-dashboard.json`。
2. **导航大盘联动**：已在 `roles/docker/observability-server/files/homepage-navigation.json` 中配置 `Serverless` 快捷入口磁贴。
3. **JSON 语法校验**：
   ```bash
   jq .title roles/docker/observability-server/files/serverless-edge-cloudrun-supabase-dashboard.json
   jq .title roles/docker/observability-server/files/homepage-navigation.json
   ```
