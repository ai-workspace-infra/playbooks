# 边缘端 Vector Agent 采集架构 (TLDR)

本文档整理了 `vector-agent` 在可观测性架构中的定位、采集源配置及数据路由转发逻辑。

## 1. 核心架构拓扑
```mermaid
graph TD
    subgraph 边缘节点 (Client)
        NodeExporter[Node Exporter 等本地探针]
        AppLogs[应用本地日志文件 / Journald]
        AppTrace[应用 OTLP 埋点数据]
        Vector[Vector Agent]
        
        NodeExporter -->|Scrape| Vector
        AppLogs -->|Tail| Vector
        AppTrace -->|Push| Vector
    end

    subgraph 中心服务端 (Observability Server)
        Caddy[Caddy HTTPS 网关]
    end

    Vector -->|Metrics: HTTPS Push| Caddy
    Vector -->|Logs: HTTPS Push| Caddy
    Vector -->|Traces: HTTPS Push| Caddy
```

## 2. 数据采集端 (Sources) 职责

Vector 在这套架构中扮演了“大一统”数据采集器的角色，替代了传统的 `Promtail` / `Filebeat` / `Jaeger Agent` 多进程模式。

*   **指标采集 (Metrics):** Vector 会定期抓取本机的各种 Exporter（例如 Node Exporter 获取 CPU/内存，Process Exporter 获取进程状态）暴露的 `/metrics` 接口。
*   **日志采集 (Logs):** Vector 监听指定的目录（如 `/var/log/**/*.log`）或 systemd 的 journald，实时增量读取最新的业务和系统日志。
*   **链路采集 (Traces):** Vector 可以开启 OTLP Source 监听本地端口（如 `4317` 或 `4318`），接收本机应用微服务产生的分布式链路数据。

## 3. 数据路由转发 (Sinks) 及认证方式 (Auth)

采集到的数据经过 Vector 内部清洗后，会统一通过公网 HTTPS 推送到 `observability.svc.plus` 网关。**由于走公网传输，客户端与服务端之间严格通过认证机制保障安全。**

### 目标端与协议
| 数据类型 | 推送协议 / Sink 类型 | 目标服务端接入点 (Endpoint) | 后端实际处理组件 |
| :--- | :--- | :--- | :--- |
| **Metrics** | `prometheus_remote_write` | `https://observability.svc.plus/ingest/metrics/api/v1/write` | **VictoriaMetrics** |
| **Logs** | `http` (或 `elasticsearch`) | `https://observability.svc.plus/ingest/logs/insert/jsonline` | **VictoriaLogs** |
| **Traces** | `opentelemetry` (OTLP/HTTP) | `https://observability.svc.plus/ingest/otlp/v1/traces` | **VictoriaTraces** |

### 采集端认证方式 (Authentication Configuration)

Vector 向 Caddy 推送数据时，采用 **Basic Auth (或 Bearer Token)** 进行身份验证。在 Vector 的配置文件 (`vector.toml`) 中，为每一个 Sink 节点统一配置 Auth 策略：

```toml
# 以发送 Logs 的 Sink 为例
[sinks.to_victorialogs]
type = "http"
inputs = ["app_logs"]
uri = "https://observability.svc.plus/ingest/logs/insert/jsonline"
encoding.codec = "json"

# 配置 HTTP Basic 认证
[sinks.to_victorialogs.auth]
strategy = "basic"
user = "${VECTOR_AUTH_USER}"
password = "${VECTOR_AUTH_PASSWORD}"

# 或者如果服务端使用 Token 认证
# [sinks.to_victorialogs.auth]
# strategy = "bearer"
# token = "${VECTOR_BEARER_TOKEN}"
```

**安全实践：** 密码或 Token 建议通过 Ansible 部署时以环境变量或受保护文件的形式注入 Systemd，避免明文写在配置文件中。
