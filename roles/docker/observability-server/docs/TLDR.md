# 纯血 Victoria 全家桶可观测性服务端架构 (TLDR)

本文档整理了 `observability-server` 中各组件的架构拓扑、统一网关映射、采集认证机制以及可视化接入配置。

## 1. 核心架构拓扑
```mermaid
graph TD
    Client[边缘端 Vector / 浏览器] -->|HTTPS 443 (Basic Auth / Token)| Caddy[Caddy 网关]
    Caddy -->|/ingest/metrics/| VM[VictoriaMetrics:8428]
    Caddy -->|/ingest/logs/| VL[VictoriaLogs:9428]
    Caddy -->|/ingest/otlp/| VT_Ingest[VictoriaTraces:4318]
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
| **`/ingest/metrics/*`** | `handle_path /ingest/metrics/*`| `victoria-metrics` | `8428` | **写入**：接收 Remote Write 时序指标 |
| **`/vmetrics/*`** | `handle_path /vmetrics/*`| `victoria-metrics` | `8428` | **查询**：Grafana 读取 Metrics (PromQL) |
| **`/ingest/logs/*`** | `handle_path /ingest/logs/*` | `victoria-logs` | `9428` | **写入**：接收 Vector 等 JSON 日志推送 |
| **`/vlogs/*`** | `handle_path /vlogs/*` | `victoria-logs` | `9428` | **查询**：Grafana 读取 Logs (LogQL) |
| **`/ingest/otlp/*`** | `handle_path /ingest/otlp/*` | `victoria-traces` | `4318` | **写入**：接收 OTLP 格式链路数据 |
| **`/vtraces/*`** | `handle_path /vtraces/*` | `victoria-traces` | `10428` | **查询**：向外部/Grafana暴露 Jaeger API |
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

## 4. Grafana 插件与数据源规划 (Data Sources)

服务端部署时，通过 `GF_INSTALL_PLUGINS` 环境变量在 Grafana 启动时自动下载插件，并通过 Provisioning 自动配置好三大数据源，实现开箱即用的“黄金三角”。

### 需要预装的 Grafana 插件
*   **`victoriametrics-datasource`**: VictoriaMetrics 官方开发的 Grafana 插件。相比原生 Prometheus 数据源，它提供了更好的 Logs 查询 UI、Metrics 增强特性和自动化的全链路联动支持。

### 自动托管注入的据源 (Provisioning)

| 数据源名称 | 插件类型 (Type) | 内部接入 URL | 联动配置 (Correlation / Derived Fields) |
| :--- | :--- | :--- | :--- |
| **VictoriaMetrics** | `victoriametrics-datasource` (或 `prometheus`) | `http://victoria-metrics:8428` | 支持配置 Exemplars，当图表延迟飙升时，点击小绿点可**直接跳转**到对应的 VictoriaTraces 链路。 |
| **VictoriaLogs** | `victoriametrics-datasource` | `http://victoria-logs:9428` | 配置 Derived Fields，自动提取日志中的 `trace_id` 字段并转换为超链接。点击该链接即可**直接跳转**到 VictoriaTraces 查看瀑布流。 |
| **VictoriaTraces** | `jaeger` | `http://victoria-traces:10428` | VictoriaTraces 完美实现了 Jaeger Query API，因此在 Grafana 中可以直接使用原生 Jaeger 数据源插件对接。提供完整的瀑布流视图。 |
