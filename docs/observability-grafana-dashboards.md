# Grafana dashboard source of truth

All Grafana provisioning dashboards are versioned in this repository under
`roles/docker/observability-server/files/`. The Observability role discovers
every `*.json` file in that directory and copies it into
`/opt/observability-server/grafana/dashboards/`; Grafana loads them through the
file provisioning provider. Add, review, and update provisioning dashboards
here. Do not maintain a separate Ansible filename list or edit the deployed
copies by hand.

## Current inventory

| File | Dashboard UID | Title |
| --- | --- | --- |
| `Node-Exporter-Dashboard.json` | `StarsL-JOB-node` | Node Exporter Dashboard 20240520 通用JOB分组版 |
| `starsl-k8s-dashboard.json` | `StarsL_cn_K8S` | K8S Dashboard CN 20240513 StarsL.cn |
| `agent-ai-application-observability-dashboard.json` | `agent-ai-observability` | Agent / AI Application Observability |
| `ai-aggregator-overview.json` | `ai-aggregator-overview` | AI Aggregator Overview |
| `blackbox-exporter-dashboard.json` | `blackbox-exporter-overview` | Blackbox Exporter & SSL Probe Overview |
| `caddy-dashboard.json` | `fed94ifiv8h6oc` | Caddy |
| `dashboard.json` | `begqoward2epsf` | Xray Dashboard |
| `homepage-navigation.json` | `homepage-navigation` | 平台导航与总览 (Navigation Homepage) |
| `k6-performance-capacity-dashboard.json` | `k6-performance-capacity-overview` | k6 Performance & Capacity Stress Test Dashboard |
| `postgres-community-overview.json` | `postgres-community-overview` | PostgreSQL Community Overview |
| `postgres-exporter-dashboard.json` | `postgres-exporter-overview` | PostgreSQL Database Overview |
| `process-exporter-dashboard-with-treemap.json` | `process-exporter-with-tree` | process exporter dashboard with treemap |
| `serverless-edge-cloudrun-supabase-dashboard.json` | `serverless-fullstack-architecture` | 全栈无服务器与边缘架构总览 (Serverless & Edge Full-Stack Topology) |
| `victoria-logs-overview.json` | `victoria-logs-overview` | Victoria Logs Overview |
| `victoria-traces-dashboard.json` | `victoria-traces-overview` | VictoriaTraces Distributed Tracing APM |

The AI Aggregator collection and dashboard contract is documented in
[`ai-aggregator-observability.md`](ai-aggregator-observability.md).

The StarsL K8s source is pinned and adapted by the controller-side
[`grafana_starsl_dashboard` role](../roles/platform/grafana_starsl_dashboard/README.md).
Run `import_starsl_k8s_dashboard.yml` to plan/replay its file-provisioning source
and optional GitOps ConfigMap. The role documents kube-state-metrics/kubelet
requirements, cluster/environment isolation and deliberately omitted sensitive
inventory counts. Importing the file does not prove live telemetry acceptance.

The source-host SQLite `dashboard` table contained zero rows during the
2026-09-27 migration inventory, confirming that the active dashboards were
provisioned from JSON rather than saved only in Grafana's database. Two
`serverless-edge-cloudrun-supabase-dashboard.json.*.bak` files were present on
the host; they are not `*.json`, are not part of the inventory, and are not
loaded by the provider.

Three live JSON files differed from the repository copy at inventory time:
the live Node Exporter and Xray dashboards lacked repository tags, and the live
Process Exporter dashboard used older datasource UIDs. The checked-in files are
the canonical versions and should be deployed as-is. The standalone migration
workflow verifies these UIDs/titles after deployment; it does not copy these
runtime drifted files back into Git.

The Caddy dashboard uses the `VictoriaMetrics` Prometheus-compatible datasource by default. It requires Caddy native metrics to be scraped and delivered to that VictoriaMetrics instance with `job` and `instance` labels. DataKit delivery to Guance alone does not populate this datasource.

## PostgreSQL community overview

`postgres-community-overview.json` adapts the official
[prometheus-community postgres_exporter dashboard](https://github.com/prometheus-community/postgres_exporter/blob/281cf032091aaaffded97f7df29b8ab29be051bd/postgres_mixin/dashboards/postgres-overview.json)
at commit `281cf032091aaaffded97f7df29b8ab29be051bd`. It is distributed
under [Apache License 2.0](licenses/prometheus-community-postgres-exporter-APACHE-2.0.txt).
Changes: distinct UID/title, datasource and cascading selectors, strict
nonempty label filters, instance-scope clarification, and a PG17 checkpointer
series. The original dashboard layout and existing dashboard remain available.

The datasource must contain `pg_stat_database_numbackends` with nonempty
`environment`, `cluster`, `service`, `instance` and `datname` labels. Every
metric used must carry the first four labels; database metrics also require
`datname`. Missing labels deliberately produce no data. Database selections
apply to database metrics; bgwriter and checkpointer are PostgreSQL instance
metrics without a database dimension, so the Buffers panel remains scoped to
the selected instances and does not claim per-database attribution. The TPS
panel inherits the upstream calculation of transaction commits plus rollbacks,
not SQL statement throughput.

For PostgreSQL 17, upstream's bgwriter collector no longer emits
`buffers_checkpoint`, `buffers_backend`, or `buffers_backend_fsync`: those
legacy series may be absent. The replacement checkpointer buffers series
requires the exporter `stat_checkpointer` collector, disabled by default. Its
availability depends on the deployed exporter version, collector configuration,
permissions, and actual ingestion; the dashboard alone does not enable it or
prove live data. Upstream documents PostgreSQL 17 in its CI matrix. Validate
actual series and panel results for each deployed version before acceptance.

### Online query verification status

User-provided observation: PostgreSQL 17.11 with postgres_exporter 0.17.1 has data for `buffers_alloc` and `buffers_clean`, and lacks the three legacy checkpoint/backend/backend_fsync series. This is metric-level evidence, not execution of the adapted PromQL. No online query endpoint or scoped access was supplied in this task; each expression below remains unverified online. `No data` is not zero.

| Panel | Ref | Online result |
| --- | --- | --- |
| TPS | A | Not executed |
| Rows | A | Not executed |
| Rows | B | Not executed |
| Rows | C | Not executed |
| Rows | D | Not executed |
| Rows | E | Not executed |
| Buffers (instance-wide) | A | Not executed |
| Buffers (instance-wide) | B | Not executed |
| Buffers (instance-wide) | C | Not executed |
| Buffers (instance-wide) | D | Not executed |
| Buffers (instance-wide) | E | Not executed |
| Buffers (instance-wide) | F | Not executed |
| Conflicts/Deadlocks | A | Not executed |
| Conflicts/Deadlocks | B | Not executed |
| Cache hit ratio | A | Not executed |
| Number of active connections | A | Not executed |
