# 全栈无服务器与边缘架构可观测性指南 (Serverless & Edge Full-Stack Observability Guide)

本文档阐述基于 **Cloudflare Edge + Frontend Router + 5 SSR Workers + 3 Edge Gateway Workers + GCP Cloud Run + Supabase Cloud DB (PostgreSQL)** 的端到端多环境 (`sit` / `uat` / `prod`) 可观测性架构与 Grafana Dashboard 设计。

---

## 1. 系统架构与拓扑 (System Topology)

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

### 1.1 云原生资产与控制台直达

| 组件层级 | 服务 / 标识 | 环境分布 | 控制台直达 / 资源地址 |
|---|---|---|---|
| **Edge & DNS** | Cloudflare Zone (`onwalk.net` / `svc.plus`) | SIT / UAT / PROD | [Cloudflare Workers & Pages](https://dash.cloudflare.com/e71be5efb76a6c54f78f008da4404f00/workers-and-pages) |
| **Frontend Router** | `frontend-router-{env}` | SIT / UAT / PROD | 挂载于 `console-{env}.onwalk.net` / `console.svc.plus` |
| **5 SSR Workers** | `public` / `content` / `auth` / `console` / `workspace` | 独立 Worker 边界 | 路由分流 (`/*`, `/blogs*`, `/login*`, `/panel*`, `/ai-workspace*`) |
| **Pages 静态产物** | `ai-workspace-portal-{env}.pages.dev` | SIT / UAT / PROD | 静态资源前缀 `/_next/*`, `/static/*`, `/assets/*` |
| **3 Gateway Workers** | `edge-gateway-auth` / `admin` / `core` | API Ingress | 挂载于 `accounts-{env}.onwalk.net`，支持主备回退路由 |
| **弹性计算 (Cloud Run)** | `accounts` / `content-service` / `billing-service` | GCP Project: `xworktech` | [Google Cloud Run Console](https://console.cloud.google.com/run/services?project=xworktech) (Region: `asia-northeast1`) |
| **核心数据库 (Supabase)** | PostgreSQL 15+ & Supavisor Pooler | Project: `iqkxspmhcfqmhkbjdoms` | [Supabase Project Console](https://supabase.com/dashboard/project/iqkxspmhcfqmhkbjdoms) |

---

## 2. 多环境多维度看板结构 (Dashboard Structure)

看板 UID：`serverless-fullstack-architecture`  
看板标题：`全栈无服务器与边缘架构总览 (Serverless & Edge Full-Stack Topology)`

### 2.1 模板变量 (Templating Variables)
- **`$env`**: 环境选择器 (`sit`, `uat`, `prod`, `All`)，支持无缝切换。
- **`$DS_METRICS`**: 指标数据源（默认 `victoriametrics` / `prometheus`）。
- **`$DS_LOGS`**: 日志数据源（默认 `victorialogs`）。
- **`$ssr_worker`**: 5 大 SSR 边缘工作线程筛选器 (`public`, `content`, `auth`, `console`, `workspace`)。
- **`$gateway_worker`**: 3 大 API 边界筛选器 (`auth`, `admin`, `core`)。
- **`$cloudrun_service`**: Cloud Run 后端服务筛选器 (`accounts`, `content-service`, `billing-service`)。
- **`$database`**: Postgres 数据库名筛选器 (`postgres`, `accounts`, `billing`)。

---

## 3. 分层可观测性设计与指标规范 (Telemetry Pillars)

### 3.1 全链路架构脉搏 (Architecture Pulse)
- **全站可用性 (SLA)**：综合所有端点黑盒探针成功率（`avg(probe_success{env=~"$env"}) * 100`）。
- **边缘总 QPS**：Cloudflare 接入总请求速率。
- **SSR 平均 CPU 耗时**：Worker 执行耗时基线（毫秒级）。
- **Gateway 回退比例**：Primary VPS 与 Fallback Cloud Run 的流量比例与故障转移状态。
- **Cloud Run 活跃实例**：当前伸缩实例总数。
- **Supabase 连接池饱和度**：当前活跃连接占最大连接数百分比。

### 3.2 DNS 流量与边缘接入采集 (DNS & Edge Ingress)
- **DNS 解析速率与类型**：A / AAAA / CNAME 查询 QPS 与响应状态码（`NOERROR`, `NXDOMAIN`, `SERVFAIL`）。
- **HTTP 响应状态码分布**：2xx, 3xx, 4xx, 5xx 堆叠趋势图。
- **边缘缓存命中率与带宽**：Cached vs Uncached Bandwidth (Bps)。
- **WAF 与安全防护**：威胁拦截事件、Bot 防护计数与速率限制触发。

### 3.3 终端可用性与 SSL 证书黑盒探测 (Endpoint Synthetic Probe)
- **全域探针状态矩阵**：
  - `console.{env}` (Console Portal 探测)
  - `accounts.{env}` (Accounts Auth / Gateway 探测)
  - Cloud Run 直连健康检查探测
- **SSL 证书倒计时 (天)**：
  - 触发黄色告警阈值：`< 15` 天
  - 触发红色严重告警阈值：`< 7` 天
- **探针时延分段**：DNS 查找、TCP 建连、TLS 握手、TTFB 首字节、传输耗时。

### 3.4 Cloudflare Frontend Router 与 5 大 SSR Workers
- **分发吞吐量 (QPS)**：Frontend Router 命中 Pages vs 5 SSR Workers。
- **CPU 耗时 (p50/p95/p99)**：各 SSR Worker 内部渲染耗时。
- **子请求与外部调用**：Worker 发起的 Origin / KV / DB 子请求速率。
- **Worker 错误数**：4xx/5xx 与 Worker Unhandled Exceptions。

### 3.5 Cloudflare Edge Gateway (3 Workers)
- **3 大边界流量**：`/api/auth/*` (Auth), `/api/admin/*` (Admin), `/api/*` (Core)。
- **JWT 鉴权耗时**：Gateway 解析与验证 JWT 签名耗时（p95 ms）。
- **401/403 拦截率**：鉴权失败与权限拒绝速率。
- **Failover 回退路由**：主节点不可用时平滑向 Cloud Run fallback 的请求数。

### 3.6 GCP Cloud Run 运行的服务 (Project: xworktech)
- **服务请求与延迟**：`accounts`, `content-service`, `billing-service` 请求 QPS 与 p95 响应耗时。
- **容器伸缩与冷启动**：实例数（Active Instances）与冷启动（Cold Start / Container Startup）计数。
- **资源使用率**：CPU 利用率 (%) 与 Memory 利用率 (%)。

### 3.7 ★ Supabase Cloud DB / PostgreSQL 深度性能 (重点关注)
- **连接数与连接池负载**：
  - 活跃连接数（`state=active`）、空闲连接（`state=idle`）、事务中空闲（`state=idle in transaction`）。
  - Supavisor / PgBouncer 客户端等待队列与池饱和度。
- **TPS 事务吞吐量**：Commits/s vs Rollbacks/s（回滚率异常升高报警）。
- **共享内存缓存命中率 (Cache Hit Ratio)**：
  - 目标值：`> 99%`（低于 `95%` 触发性能调优告警）。
- **SQL 执行耗时分位数**：`pg_stat_statements` 平均查询耗时与 p95 耗时。
- **死元组与自动清理**：`n_dead_tup` 膨胀趋势与 Autovacuum 触发频率。
- **存储与 WAL**：数据库磁盘占用量（GB）与 WAL 生成速率（Bytes/s）。

### 3.8 全栈统一日志流下钻 (VictoriaLogs)
- **错误与警告组件分布**：按 `source_type` (`cloudflare`, `cloud_run`, `supabase`) 聚合错误日志。
- **Supabase DB 慢查询与报错日志**：筛选 `duration > 500ms` 与 `ERROR` / `FATAL` 级别数据库日志。
- **Cloud Run & Edge 运行时日志**：实时日志流，支持按环境、服务、状态码直接过滤。

---

## 4. 采集器与数据导出链路配置 (Data Pipeline)

```
[Cloudflare Edge]   --> (Logpush / Analytics API)    --> [Vector / Prometheus Exporter] --> [VictoriaMetrics / Logs]
[GCP Cloud Run]     --> (Cloud Monitoring / OTel)    --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Supabase DB]       --> (postgres_exporter / Drain)  --> [Prometheus Scraper]          --> [VictoriaMetrics / Logs]
[Blackbox Exporter] --> (Synthetic HTTPS Probes)     --> [Prometheus Scraper]          --> [VictoriaMetrics]
```

---

## 5. 验证与交付清单

1. **Grafana 自动装载**：`playbooks/roles/docker/observability-server/tasks/main.yml` 已配置自动下发 `serverless-edge-cloudrun-supabase-dashboard.json`。
2. **首页导航联动**：`homepage-navigation.json` 已集成 `Serverless` 快捷入口磁贴。
3. **多环境过滤**：仪表板在 `sit`、`uat`、`prod` 下均可独立过滤与聚合呈现。
