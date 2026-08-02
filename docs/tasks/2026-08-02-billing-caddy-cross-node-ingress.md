# UAT Billing Caddy 跨节点入口

## 背景

UAT 的 `billing-service` 跑在 web-saas 主机，`xray-exporter` 与
`agent-svc-plus` 跑在 agent-proxy 主机。Billing 的数据采集仍是：

```text
Xray -> xray-exporter ->(Billing 拉取 window API)-> billing-service -> PostgreSQL -> Accounts -> Portal
agent-svc-plus ->(触发 collect/reconcile job)-> billing-service
```

Billing 不读取 Prometheus/Grafana，也不把 observability 做成计费写路径的依赖。

## 改动

`roles/vhosts/web_saas_host_config` 新增可选的 Billing Caddy 入口：

- `WEB_SAAS_BILLING_DOMAIN`：`billing-<env>.<domain>`；
- `WEB_SAAS_BILLING_ALLOWED_CIDRS`：agent-proxy 地址的 `/32` CIDR；
- 两者同时存在时，Caddy 将请求反代到 compose 网络内的 `billing:8081`；
- 非 agent-proxy 源地址返回 `403`；两个变量任一为空则不生成入口。

同一轮还在 agent-proxy Caddy 增加了两个只读 Exporter 路由：

- `/xray-exporter/xhttp/*` -> `127.0.0.1:8080`；
- `/xray-exporter/tcp/*` -> `127.0.0.1:8081`。

因此 Billing 的 UAT 默认 source 使用
`https://agent-proxy.<domain>/xray-exporter/{xhttp,tcp}`，不再依赖公网裸端口
8080/8081；Exporter 原有 Bearer token 认证仍由 Billing 发送。

这条入口只承载 agent 的 `/v1/jobs/collect-and-rate` 与
`/v1/jobs/reconcile` 触发请求。Billing 的 Exporter source 仍通过
`EXPORTER_SOURCES_JSON` 注入，并且必须使用可认证的 HTTPS Exporter 地址；本改动不
改变 Exporter snapshot API，也不接触生产 Xray。

## 部署验收

在 UAT 发布后，从 agent-proxy 节点检查：

```bash
curl --fail --silent --show-error \
  -X POST https://billing-uat.onwalk.net/v1/jobs/collect-and-rate
```

在非 agent-proxy 网络或未配置允许 CIDR 时应返回 `403`。随后检查 Billing 日志、
`traffic_minute_buckets` / `billing_ledger` / `account_quota_states`，最后刷新
`https://console-uat.onwalk.net/panel/account` 验证 Accounts 聚合数据。

## 安全边界

本 PR 不新增生产域名，不修改生产 Xray/Exporter。Billing job API 当前由
agent-proxy 源 IP 限制保护；后续如果 agent client 支持服务令牌，应再叠加
`Authorization: Bearer` 校验，届时保留 Caddy CIDR 作为第二道边界。
