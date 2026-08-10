# Vector Blackbox SSL targets 修复

## 现象

Blackbox Exporter 本身可返回 `probe_*` 指标，但集中式 VictoriaMetrics 查询不到
`probe_success`、`probe_http_status_code` 和 `probe_ssl_earliest_cert_expiry`，因此
Grafana 的 Blackbox SSL 看板为空。

## 根因

`roles/vhosts/vector-agent/tasks/main.yml` 将 Blackbox defaults 放在
`blackbox_exporter_role_defaults` 命名空间下加载，而这些 defaults 的派生变量
（`blackbox_probe_domain_base`、`blackbox_probe_env`、`blackbox_probe_env_suffix`、
`blackbox_ssl_targets`）依赖未加命名空间的变量。Vector 模板直接取派生 targets 时，
目标机无法解析这些依赖，最终没有生成 Blackbox scrape source，也没有把 Blackbox
transform 接入 remote-write sink。

## 修复

在 Vector role 中按依赖顺序显式解析：

1. 域名 base 与环境名；
2. 环境后缀；
3. `blackbox_ssl_targets`。

保留 inventory/extra-vars 覆盖能力，不改变 Blackbox Exporter、Grafana 或
VictoriaMetrics 的接口。

目标集合：

- UAT (`TARGET_DOMAIN_BASE=onwalk.net`, `DEPLOY_ENV=uat`)：
  `console-uat.onwalk.net`、`accounts-uat.onwalk.net`；
- PROD (`TARGET_DOMAIN_BASE=svc.plus`, `DEPLOY_ENV=production`)：
  `jp-xhttp.svc.plus`、`tky-proxy.svc.plus`、`www.svc.plus`、
  `console.svc.plus`、`accounts.svc.plus`。

## 验证与发布

- UAT/PROD 模板离线渲染均成功，分别生成 2/5 个 Blackbox scrape source；
- 每个 source 均进入 `sinks.prometheus_remote.inputs`；
- `git diff --check` 通过；
- `deploy_observability_agent.yml` syntax-check 通过；
- 合并后使用 platform-ops-toolkit 的既有 UAT/PROD 发布入口，发布完成后逐段验证
  Vector scrape、VictoriaMetrics `probe_*` series 与 Grafana 看板。
