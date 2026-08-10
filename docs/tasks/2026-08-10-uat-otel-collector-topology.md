# UAT OTLP Trace 采集拓扑与排障记录

## 结论

每台 VPS 只保留一个负责应用 Trace 接入的 Collector 入口：

```text
Caddy
  └─ OTLP/gRPC → web-saas-otel-collector:4317
                    └─ OTLP/HTTP → https://observability.svc.plus/ingest/otlp/v1/traces
                                      └─ VictoriaTraces:10428
```

这里的 `otel-collector:4317` 是 web-saas Docker 网络内的服务名，不是公网入口。
Vector 不参与这条 Trace 链路；Vector 继续负责 Metrics、Logs 和 Billing 数据采集。

## 主机角色边界

| 主机 | 4317 角色 | 应保留的实例 | 说明 |
| --- | --- | --- | --- |
| `167.179.64.91` / `console-uat.onwalk.net` | 应用 Trace Collector | 一个 `web-saas-otel-collector`，网络地址 `otel-collector:4317` | Caddy 使用 OTLP/gRPC；Collector 使用 OTLP/HTTP 写入 VictoriaTraces |
| `46.250.251.132` / `install.svc.plus` | VictoriaTraces 后端接收端 | 不再额外部署应用 Collector | VictoriaTraces 自身监听本机 `127.0.0.1:4317/4318`；这是存储后端 receiver，不是第二个应用 Collector |
| `66.42.45.216` | Vector/业务采集节点 | 不部署重复 Trace Collector | 需要 Trace 时复用该主机规划中的唯一 Collector，不新增 Vector OTLP sink |

不要在同一台 VPS 上同时启动：

- 多个监听 `4317` 的应用 Collector；
- web-saas 私有 Collector 与另一个 systemd Collector 的重复实例；
- Vector OTLP Trace sink 与 Collector Trace pipeline 的重复转发。

## 当前配置

UAT Caddy 使用：

```text
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.05
```

Collector 使用：

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

exporters:
  otlphttp/victoriatraces:
    traces_endpoint: https://observability.svc.plus/ingest/otlp/v1/traces
```

公网入口由 observability Caddy 改写到 VictoriaTraces：

```text
/ingest/otlp/v1/traces
  → /insert/opentelemetry/v1/traces
  → victoria-traces:10428
```

## 已完成验证

- UAT web-saas 栈完整删除容器后由 Doco-CD 从 GitOps `main` 恢复成功。
- UAT 只存在一个 `web-saas-otel-collector`，Collector 状态为 running。
- Caddy OTEL endpoint 为 `http://otel-collector:4317`，无 `traces export` 连接错误。
- Caddy 带 W3C `traceparent` 的请求返回 HTTP 200。
- VictoriaTraces Jaeger API 出现 `web-saas-caddy` 服务与 `http.server.request` spans。
- VictoriaTraces OTLP protobuf counters 持续增长，说明已实际写入：
  `vt_rows_ingested_total{type="opentelemetry_traces_protobuf"}`。
- Accounts 的 PostgreSQL spans 已出现在 VictoriaTraces；应用链路继续按 Console BFF → Accounts/Billing 验证。

## 排障顺序

1. 主机：确认同一 VPS 只存在一个应用 Collector，且 `4317` 没有重复监听。
2. Caddy：确认 `OTEL_EXPORTER_OTLP_ENDPOINT` 带 `http://`，并检查 `docker logs web-saas-caddy` 是否出现 `traces export` 错误。
3. Collector：确认 `otel-collector` 能解析并监听 `4317`，配置中的 exporter 指向公网 OTLP/HTTP 入口。
4. 网关：确认 `https://observability.svc.plus/ingest/otlp/v1/traces` 返回成功，并被改写到 VictoriaTraces ingest path。
5. VictoriaTraces：查询 `/select/jaeger/api/services`、指定 Trace ID，并检查 `vt_rows_ingested_total`。
6. 应用：使用固定 W3C `traceparent` 分别访问 Console、Accounts、Billing，确认同一 Trace ID 下出现 HTTP 与 DB spans。

## 相关交付

- GitOps web-saas UAT 快照：`uat-daily-build-2026.08.10-r5`
- GitOps 版本轴：PR #136
- Playbooks OTLP 链路与 Collector 路径修复：PR #296
- UAT CD：run `31387962209`
