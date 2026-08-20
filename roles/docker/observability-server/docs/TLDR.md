# 纯血 Victoria 全家桶可观测性服务端架构 (TLDR)

本文档整理了 `observability-server` 中各组件的架构拓扑、统一网关映射、采集认证机制以及可视化接入配置。

## 1. 核心架构拓扑
```mermaid
graph TD
    Client[边缘端 Vector / 浏览器] -->|HTTPS 443 (Basic Auth / Token)| Caddy[Caddy 网关]
    Caddy -->|/ingest/metrics/| VM[VictoriaMetrics:8428]
    Caddy -->|/ingest/logs/| VL[VictoriaLogs:9428]
    Caddy -->|/ingest/otlp/v1/traces| VT_Ingest[VictoriaTraces:10428/insert/opentelemetry/v1/traces]
    Caddy -->|/grafana/| Grafana[Grafana:3030]
    Caddy -->|/vtraces/| VT_Query[VictoriaTraces:10428]
    vmalert -->|查询规则| VM
    vmalert -->|触发告警| Alertmanager[Alertmanager:9059]
```

## 2. 统一规划网关路由映射表 (Caddy -> Docker Internal)

Caddy 作为统一入口，通过不同的 Path 将外网请求分发到 Docker Compose 内部对应的容器和端口。

| 统一公网入口 (observability.svc.plus) | Caddy 指令 | Docker 内部服务 (目标组件) | 内部端口 | 核心职责 |
| :--- | :--- | :--- | :--- | :--- |
| **`/grafana/*`** | `handle /grafana/*` | `grafana` | `3000` | 全局大盘，默认根路径 `/` 也会重定向至此 |
| **`/otlp/v1/traces`** (或 `/v1/traces`) | `handle @otlp_traces` + rewrite | `victoria-traces` | `10428` | **写入**：OpenTelemetry Traces 标准 OTLP/HTTP 接入 |
| **`/otlp/v1/logs`** (或 `/v1/logs`) | `handle @otlp_logs` + rewrite | `victoria-logs` | `9428` | **写入**：OpenTelemetry Logs 标准 OTLP/HTTP 接入 |
| **`/otlp/v1/metrics`** (或 `/v1/metrics`)| `handle @otlp_metrics` + rewrite | `victoria-metrics` | `9090` | **写入**：OpenTelemetry Metrics 标准 OTLP/HTTP 接入 |
| **`/api/v1/write`** | `handle @prom_write` | `victoria-metrics` | `9090` | **写入**：Prometheus Remote Write 接入 |
| **`/insert/jsonline`** | `handle @vlogs_insert` | `victoria-logs` | `9428` | **写入**：VictoriaLogs JSON Lines 格式流式直推 |
| **`/ingest/metrics/*`** | `handle_path /ingest/metrics/*`| `victoria-metrics` | `9090` | **写入**：兼容原有 Remote Write 指标写入 |
| **`/vmetrics/*`** | `handle_path /vmetrics/*`| `victoria-metrics` | `9090` | **查询**：Grafana 读取 Metrics (PromQL / MetricsQL) |
| **`/ingest/logs/*`** | `handle_path /ingest/logs/*` | `victoria-logs` | `9428` | **写入**：兼容原有 Vector 等 JSON 日志推送 |
| **`/vlogs/*`** | `handle_path /vlogs/*` | `victoria-logs` | `9428` | **查询**：Grafana 读取 Logs (LogsQL) |
| **`/ingest/otlp/v1/traces`** | `handle /ingest/otlp/v1/traces*` + rewrite | `victoria-traces` | `10428` | **写入**：兼容原有 OTLP/HTTP 写入 |
| **`/vtraces/*`** | `handle_path /vtraces/*` | `victoria-traces` | `10428` | **查询**：向外部/Grafana暴露 TraceQL / Jaeger API |
| **`/vmalert/*`** | `handle_path /vmalert/*` | `vmalert` | `8880` | **引擎**：告警规则计算引擎 |
| **`/alertmgr/*`** | `handle_path /alertmgr/*` | `alertmanager` | `9093` | **路由**：告警去重、分组与分发 |
| **`/blackbox/*`** | `handle_path /blackbox/*` | `blackbox-exporter`| `9115` | **探针**：主动网络及接口拨测 |

## 3. 采集端与服务端的认证方式 (Authentication)

为了保证公网传输的安全，采集端（Vector）向服务端（Caddy）发送数据时，必须进行安全认证。

*   **服务端 (Caddy) 配置认证：**
    在 Caddyfile 中，为所有 `/ingest/*` 路径配置 **Basic Authentication**（基础 HTTP 认证）或验证统一的 Authorization Token 头。
    ```caddyfile
    # 示例: Caddy 开启 Ingest 认证
    handle /ingest/* {
        basicauth {
            # username: password_hash
            vector_agent $2a$14$xxxxxxxxxxxxx
        }
        # 然后再按路径 route 到具体服务
    }
    ```
*   **客户端 (Vector) 配置认证：**
    Vector 端的各个 Sink 组件需要配置对应的鉴权凭据，每次 Push 请求会带上认证 Header：
    ```toml
    [sinks.to_metrics.auth]
    strategy = "basic"
    user = "vector_agent"
    password = "${VECTOR_PASSWORD}"
    ```
*   *可选进阶方案：* 如果对安全性要求极高，可以在 Caddy 和 Vector 之间配置 mTLS（双向证书认证），Caddy 仅放行持有受信任客户端证书的流量。

## 4. 线上服务端差异预检 (Dry Run)

针对 `observability.svc.plus` 执行 Ansible diff/check mode 时，使用具备读取
`kv/data/observability/mcp` 权限的临时 Vault Token。该命令不会修改线上服务：

```bash
cd /Users/shenlan/workspaces/ai-workspace-infra/playbooks

VAULT_ADDR=https://vault.svc.plus \
VAULT_TOKEN='<有效token>' \
ansible-playbook -i inventory.ini deploy_observability_server.yml \
  -e observability_server_hosts=observability_hosts \
  -l observability_hosts -D -C
```

`observability_hosts` 是 inventory 中对应 `observability.svc.plus` 的主机组。
不要将真实 Vault Token 写入仓库、命令历史或文档。

Vault 路径 `kv/data/observability/mcp` 还必须包含
`GRAFANA_SERVICE_ACCOUNT_TOKEN`。该 token 仅由 playbook 写入线上
`/opt/observability-server/mcp-grafana.env`，用于 Grafana MCP 调用 Grafana
API，不应写入 Git。
`-C` 预检在该字段缺失时只告警并继续；真实部署仍会 fail-closed，必须先补齐
该 Vault 字段。

## 5. Grafana 插件与数据源规划 (Data Sources)

服务端部署时，通过 `GF_INSTALL_PLUGINS` 环境变量在 Grafana 启动时自动下载插件，并通过 Provisioning 自动配置好三大数据源，实现开箱即用的“黄金三角”。

### 需要预装的 Grafana 插件
*   **`victoriametrics-datasource`**: VictoriaMetrics 官方开发的 Grafana 插件。相比原生 Prometheus 数据源，它提供了更好的 Logs 查询 UI、Metrics 增强特性和自动化的全链路联动支持。

### 自动托管注入的据源 (Provisioning)

| 数据源名称 | 插件类型 (Type) | 内部接入 URL | 联动配置 (Correlation / Derived Fields) |
| :--- | :--- | :--- | :--- |
| **VictoriaMetrics** | `victoriametrics-datasource` (或 `prometheus`) | `http://victoria-metrics:8428` | 支持配置 Exemplars，当图表延迟飙升时，点击小绿点可**直接跳转**到对应的 VictoriaTraces 链路。 |
| **VictoriaLogs** | `victoriametrics-datasource` | `http://victoria-logs:9428` | 配置 Derived Fields，自动提取日志中的 `trace_id` 字段并转换为超链接。点击该链接即可**直接跳转**到 VictoriaTraces 查看瀑布流。 |
| **VictoriaTraces** | `jaeger` | `http://victoria-traces:10428` | VictoriaTraces 完美实现了 Jaeger Query API，因此在 Grafana 中可以直接使用原生 Jaeger 数据源插件对接。提供完整的瀑布流视图。 |
