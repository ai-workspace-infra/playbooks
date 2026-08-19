# 全栈无服务器与边缘架构可观测性体系指南 (Serverless & Edge Full-Stack Architecture Guide)

本文档系统性阐述 **真正的 Serverless 全栈请求链路（`https://console-cloudflare-uat.onwalk.net/`）**：由 **Cloudflare Edge + Frontend Router + 5 SSR Workers + 3 Edge Gateway Workers + GCP Cloud Run + Supabase Cloud DB (PostgreSQL)** 构成的全栈无服务器架构，并清晰界定 **Serverless 弹性运行时** 与 **生产 VPS (Selfhost) 运行时** 的边界及可观测性接入方案。

---

## 1. 架构拓扑与请求链路 (Serverless Request Chain)

### 1.1 真正的 Serverless 全栈请求链路 (`console-cloudflare-*`)
在 Serverless 模式下，用户端请求不会直接进入 VPS 单体服务，而是由 Cloudflare 边缘全球网络接管：

```
                用户终端访问: https://console-cloudflare-uat.onwalk.net/
                                          |
                         Cloudflare Edge (Tokyo NRT / Anycast)
                                          |
                      Frontend Router Worker (frontend-router-uat)
                                          |
                +-------------------------+-------------------------+
                |                                                   |
           SSR Workers (OpenNext 5 Workers)                Edge Gateway (3 Workers)
      - public (/):    frontend-ssr-public-uat        - auth:  edge-gateway-auth-uat (/api/auth/*)
      - content:       frontend-ssr-content-uat       - admin: edge-gateway-admin-uat (/api/admin/*)
      - auth:          frontend-ssr-auth-uat          - core:  edge-gateway-core-uat (/api/*)
      - console:       frontend-ssr-console-uat                     |
      - workspace:     frontend-ssr-workspace-uat            GCP Cloud Run (asia-northeast1)
                |                                     - accounts / content / billing
      Pages (ai-workspace-portal-uat)                               |
      - 静态资源 (/_next/*, /static/*)                ★ Supabase Cloud DB (PostgreSQL 15+)
                                                         (重点关注 · 核心数据底座)
```

---

## 2. Serverless 运行时 vs 生产 VPS 运行时对比 (Runtime Boundary Matrix)

| 维度 / 角色 | ⚡ Serverless 弹性架构 (Serverless Runtime) | 🏢 生产 VPS 自建架构 (VPS Selfhost Runtime) |
|---|---|---|
| **Console 访问入口** | `https://console-cloudflare-uat.onwalk.net/` | `https://console-vps-uat.onwalk.net/` / `https://console.svc.plus/` |
| **Accounts / API 入口** | `https://accounts-cloudflare-uat.onwalk.net/` | `https://accounts-vps-uat.onwalk.net/` / `https://accounts.svc.plus/` |
| **边缘路由层** | Cloudflare `frontend-router-uat` Worker | VPS Caddy 反向代理网关 |
| **前端 SSR 渲染** | 5 大 OpenNext SSR Workers (`frontend-ssr-*`) | 单体 Node.js / Next.js Docker 容器 (`portal-dashboard`) |
| **前端静态资源** | Cloudflare Pages (`ai-workspace-portal-uat.pages.dev`) | 本地 Docker Nginx / Caddy 静态卷挂载 |
| **后端 API 计算** | GCP Cloud Run 无服务器容器（按需弹性伸缩，Project: `xworktech`） | VPS 本地 Docker 容器 (`accounts:8080`, `billing:8080`) |
| **核心数据库** | **Supabase Cloud DB** (PostgreSQL 15+ & Supavisor, Project: `iqkxspmhcfqmhkbjdoms`) | VPS 本地 PostgreSQL (`postgresql-svc-plus:5432`) |
| **网络承载** | Anycast 全球边缘网络 + HTTPS/2 / HTTP/3 + TLS 1.3 | 单机 VPS IP (`46.250.251.132` / `167.179.110.129`) |

---

## 3. 多环境路由与域名契约 (Multi-Environment Matrix)

| 环境 | 控制平面 | Serverless 门户入口 (Console Domain) | Serverless 网关入口 (Accounts Domain) | Pages 静态源站 | Cloud Run 区域 |
|---|---|---|---|---|---|
| **SIT** | Cloudflare DNS | `console-cloudflare-sit.onwalk.net` | `accounts-cloudflare-sit.onwalk.net` | `ai-workspace-portal-sit.pages.dev` | `asia-northeast1` |
| **UAT** | Cloudflare DNS | `console-cloudflare-uat.onwalk.net` | `accounts-cloudflare-uat.onwalk.net` | `ai-workspace-portal-uat.pages.dev` | `asia-northeast1` |
| **PROD** | Cloudflare DNS | `console-cloudflare-prod.svc.plus` | `accounts-cloudflare-prod.svc.plus` | `ai-workspace-portal-prod.pages.dev` | `asia-northeast1` |

---

## 4. 监控接入与认证细节 (Authentication & Ingestion TL;DR)

为了保证公网传输与多环境采集的安全性，所有外部组件向统一服务端（`https://observability.svc.plus`）推送数据时，均严格遵循 Vault 凭据隔离与认证通道规范。

```
[Cloudflare Edge]   --> (Logpush / Analytics API)    --> [Vector / Prometheus Exporter] --> [VictoriaMetrics / Logs]
[GCP Cloud Run]     --> (Cloud Monitoring / OTel)    --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Supabase DB]       --> (postgres_exporter / Drain)  --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Blackbox Exporter] --> (Synthetic HTTPS Probes)     --> [Prometheus Scraper]          --> [VictoriaMetrics]
```

### 4.1 链路一：Cloudflare Edge 接入与认证
1. **Cloudflare Logpush (边缘日志直推)**：
   - **认证方式**：HTTP Header 注入 `Authorization: Basic <base64(vector_user:vector_pass)>` 或 Bearer Token。
   - **推送终点**：`https://observability.svc.plus/ingest/logs/insert/jsonline?_msg_field=message&_stream_fields=zone,worker,status`
   - **凭据管理**：从 Vault 路径 `kv/data/<env>/serverless/cloudflare` 读取 `CLOUDFLARE_LOGPUSH_AUTH_TOKEN`。
2. **Cloudflare Analytics & DNS 指标 (GraphQL API 轮询)**：
   - **认证方式**：API Token（`Authorization: Bearer ${CLOUDFLARE_API_TOKEN}`），权限包含 `Analytics:Read`, `Zone:Read`, `Workers Analytics:Read`。
   - **凭据来源**：Vault 注入 `CLOUDFLARE_API_TOKEN` 与 Account ID `e71be5efb76a6c54f78f008da4404f00`。

---

### 4.2 链路二：GCP Cloud Run 计算指标与应用日志接入
1. **Cloud Monitoring / 指标拉取**：
   - **认证方式**：GitHub OIDC $\rightarrow$ Vault JWT $\rightarrow$ GCP Workload Identity 换取短效 Access Token，赋予 `roles/monitoring.viewer` 角色。
   - **Vault 路径**：`kv/data/<env>/serverless/gcp/`（包含 Provider、Service Account 与 Project ID `xworktech`）。
2. **Cloud Run 应用日志 (Log Router / Sink)**：
   - **认证方式**：GCP Log Sink 经 HTTPS Webhook 转发至 Vector 网关，携带 Basic Auth Header（`vector_agent:<password>`）。
   - **日志打标**：自动打标 `source_type="cloud_run"`, `service_name="accounts|content-service|billing-service"`。

---

### 4.3 链路三：Supabase Cloud DB (PostgreSQL) 深度监控接入
1. **数据库指标采集 (postgres_exporter)**：
   - **认证方式**：PostgreSQL SCRAM-SHA-256 密码认证 + 强制 TLS（`sslmode=require`）。
   - **连接目标**：
     - 直连 Direct URL：`postgres://postgres:<DB_PASS>@db.iqkxspmhcfqmhkbjdoms.supabase.co:5432/postgres?sslmode=require`
     - 连接池 Pooler URL：`postgres://postgres.iqkxspmhcfqmhkbjdoms:<DB_PASS>@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require`
   - **只读权限**：`GRANT pg_read_all_stats TO postgres_exporter; GRANT SELECT ON pg_stat_statements TO postgres_exporter;`
   - **凭据路径**：Vault `kv/data/<env>/serverless/supabase`。
2. **Supabase 慢查询与报错日志 (Log Drains)**：
   - **认证方式**：Supabase Webhook Destination 添加 Custom Headers（`Authorization: Basic <base64>`）直推 Vector Ingest，自动提取慢查询执行时长与 SQL 语句。

---

### 4.4 链路四：Blackbox Exporter 终端可用性探测
1. **黑盒探针执行**：
   - **认证策略**：对 Serverless 公网域名（`https://console-cloudflare-uat.onwalk.net`、`https://accounts-cloudflare-uat.onwalk.net` 等）发起无凭据探测（模拟真实终端访问），校验 HTTP 响应状态码及 TLS/SSL 证书有效天数。
2. **Prometheus Scraper**：
   - **认证方式**：本地 `127.0.0.1:9115` 或 Caddy `/blackbox/*` 经 Basic Auth 认证抓取，存入 VictoriaMetrics（`job="blackbox"`）。

---

## 5. Grafana 大盘概览 (Dashboard Specifications)

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

## 6. 部署与装载验证 (Verification)

1. **Ansible 自动装载**：已在 `roles/docker/observability-server/tasks/main.yml` 的 `Copy Grafana dashboards` 任务中注册 `serverless-edge-cloudrun-supabase-dashboard.json`。
2. **导航大盘联动**：已在 `roles/docker/observability-server/files/homepage-navigation.json` 中配置 `Serverless` 快捷入口磁贴。
3. **JSON 语法校验**：
   ```bash
   jq .title roles/docker/observability-server/files/serverless-edge-cloudrun-supabase-dashboard.json
   jq .title roles/docker/observability-server/files/homepage-navigation.json
   ```
