# 全栈无服务器与边缘架构可观测性体系指南 (Serverless & Edge Full-Stack Architecture Guide)

本文档系统性阐述 **Cloudflare Edge + Frontend Router + 5 SSR Workers + 3 Edge Gateway Workers + GCP Cloud Run + Supabase Cloud DB (PostgreSQL)** 的全栈无服务器架构，以及覆盖多环境（`sit` / `uat` / `prod`）的可观测性监控接入方案。

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

## 3. 监控与可观测性接入方案 (Monitoring Ingestion Runbook)

### 3.1 DNS 与 Cloudflare 边缘指标采集
1. **指标流 (Prometheus / VictoriaMetrics)**：
   - 通过 Cloudflare GraphQL Analytics API / Prometheus Cloudflare Exporter 定期拉取 DNS 解析速率（`cloudflare_dns_queries_total`）、Zone 状态码分布（`cloudflare_zone_requests_status_total`）、缓存与带宽（`cloudflare_zone_bandwidth_cached_bytes`）。
2. **边缘日志流 (VictoriaLogs)**：
   - 启用 Cloudflare Logpush 将 HTTP Request Logs 投递至 Vector 采集网关，Vector 标准化后写入 VictoriaLogs（标签：`source_type="cloudflare"`, `env="uat"`, `zone="onwalk.net"`）。

### 3.2 终端可用性与黑盒探针 (Blackbox Exporter)
1. **探针配置**：
   - 由 `roles/vhosts/blackbox_exporter` 管理探针配置 `blackbox.yml`。
   - 自动探测各环境域名：
     - `https://console-{env}.onwalk.net`
     - `https://accounts-{env}.onwalk.net`
     - `https://ai-workspace-portal-{env}.pages.dev`
     - Cloud Run 直连健康检查接口
2. **核心指标**：
   - `probe_success`：端点可用性（0: DOWN, 1: UP）。
   - `probe_duration_seconds`：端点响应耗时（DNS、TCP、TLS、TTFB、Transfer）。
   - `probe_ssl_earliest_cert_expiry`：SSL 证书到期倒计时（天）。

### 3.3 5 大 SSR Workers 与 3 大 Edge Gateway 采集
1. **Worker 性能指标**：
   - `cloudflare_worker_requests_count{worker="frontend-ssr-public-uat"}`：请求速率。
   - `cloudflare_worker_cpu_time_us_sum / cloudflare_worker_cpu_time_us_count`：平均 CPU 渲染耗时（ms）。
   - `cloudflare_worker_errors_count`：未捕获异常与 5xx 错误。
   - `edge_gateway_upstream_requests_total{upstream="primary|fallback"}`：上游分发与回退路由比例。
   - `edge_gateway_auth_duration_seconds_bucket`：JWT 鉴权耗时分位数。
2. **Worker 执行日志**：
   - Cloudflare Workers Tail / Logpush 流入 VictoriaLogs（`source_type="cloudflare"`, `worker="edge-gateway-core"`）。

### 3.4 GCP Cloud Run 计算指标与日志 (Project: xworktech)
1. **GCP 监控指标导出**：
   - 通过 OpenTelemetry Collector / GCP Cloud Monitoring Exporter 采集：
     - `cloud_run_request_count` / `run_googleapis_com_request_count`
     - `cloud_run_container_instance_count`：活跃实例伸缩
     - `cloud_run_container_startup_latencies_count`：容器冷启动频次
     - `cloud_run_container_cpu_utilizations`、`cloud_run_container_memory_utilizations`：CPU 与内存利用率
2. **容器应用日志**：
   - Google Cloud Logging 经 Log Sink 转发到 Vector，入库 VictoriaLogs（`source_type="cloud_run"`, `service_name="accounts"`）。

### 3.5 ★ Supabase Cloud DB (PostgreSQL) 深度性能监控 (重点关注)
1. **指标链路 (Prometheus Scraper -> postgres_exporter)**：
   - 连接 Supabase Direct URL (`db.iqkxspmhcfqmhkbjdoms.supabase.co:5432`) 与 Supavisor Pooler (`aws-0-ap-northeast-1.pooler.supabase.com:6543`)。
   - 采集核心数据库指标：
     - **连接负载**：`pg_stat_activity_count`（`active`, `idle`, `idle in transaction`）、`pg_settings_max_connections`、`supavisor_active_client_connections`。
     - **事务吞吐量**：`pg_stat_database_xact_commit` 与 `pg_stat_database_xact_rollback`（监测回滚率异动）。
     - **缓存命中率**：`pg_stat_database_blks_hit / (blks_hit + blks_read) * 100`（维持 > 99%）。
     - **查询耗时分位数**：`pg_stat_statements_total_exec_time / pg_stat_statements_calls` 与 `pg_stat_activity_query_duration_seconds_bucket`（p50/p95/p99）。
     - **死元组与表膨胀**：`pg_stat_user_tables_n_dead_tup` 与 `pg_stat_user_tables_autovacuum_count`。
     - **存储容量与 WAL**：`pg_database_size_bytes` 与 `pg_stat_wal_wal_bytes`。
2. **Supabase 数据库日志与慢查询**：
   - Supabase Log Drains 转发 Postgres 慢查询（`duration > 500ms`）与 `ERROR`/`FATAL` 日志至 VictoriaLogs（`source_type="supabase"`）。

---

## 4. Grafana 大盘概览 (Dashboard Specifications)

看板文件：`roles/docker/observability-server/files/serverless-edge-cloudrun-supabase-dashboard.json`  
UID：`serverless-fullstack-architecture`

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
