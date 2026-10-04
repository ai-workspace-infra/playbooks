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
| `agent-ai-application-observability-dashboard.json` | `agent-ai-observability` | Agent / AI Application Observability |
| `ai-aggregator-overview.json` | `ai-aggregator-overview` | AI Aggregator Overview |
| `blackbox-exporter-dashboard.json` | `blackbox-exporter-overview` | Blackbox Exporter & SSL Probe Overview |
| `dashboard.json` | `begqoward2epsf` | Xray Dashboard |
| `homepage-navigation.json` | `homepage-navigation` | 平台导航与总览 (Navigation Homepage) |
| `k6-performance-capacity-dashboard.json` | `k6-performance-capacity-overview` | k6 Performance & Capacity Stress Test Dashboard |
| `postgres-exporter-dashboard.json` | `postgres-exporter-overview` | PostgreSQL Database Overview |
| `process-exporter-dashboard-with-treemap.json` | `process-exporter-with-tree` | process exporter dashboard with treemap |
| `serverless-edge-cloudrun-supabase-dashboard.json` | `serverless-fullstack-architecture` | 全栈无服务器与边缘架构总览 (Serverless & Edge Full-Stack Topology) |
| `victoria-logs-overview.json` | `victoria-logs-overview` | Victoria Logs Overview |
| `victoria-traces-dashboard.json` | `victoria-traces-overview` | VictoriaTraces Distributed Tracing APM |

The AI Aggregator collection and dashboard contract is documented in
[`ai-aggregator-observability.md`](ai-aggregator-observability.md).

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
