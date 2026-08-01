# UAT xray-exporter 部署契约审计

> Status: 🟡 Exporter [#3](https://github.com/ai-workspace-xstream/xray-exporter/pull/3) [OPEN]；本仓为部署契约建议；未修改 UAT 主机、GitOps main 或生产 `svc.plus`
> Date: 2026-08-01
> Related: Billing [#24](https://github.com/ai-workspace-services/billing-service/pull/24) [MERGED]；Exporter [#3](https://github.com/ai-workspace-xstream/xray-exporter/pull/3) [OPEN]；本仓后续 feature PR 待创建

## 结论

Billing 的多 inbound UUID 聚合已经在 Billing main 合入，但当前 playbooks role 仍是旧 exporter 部署契约，不能证明 UAT 上存在 Billing 所需的 authenticated `/v1/snapshots/window` API，也不能证明 Billing 容器能访问 agent-proxy 上的 exporter。

## 只读证据

- `roles/vhosts/xray-exporter/defaults/main.yml` 声明 XHTTP `127.0.0.1:8080` 与 TCP `127.0.0.1:8081` 两个实例。
- `roles/vhosts/xray-exporter/tasks/main.yml` 从 `compassvpn/xray-exporter` 的 `v0.6.0` release 下载二进制，不从 control-plane `xray-exporter` 源码构建或固定对应 commit。
- `roles/vhosts/xray-exporter/templates/xray-exporter.service.j2` 的 `ExecStart` 仍使用 `-l/-e/-u/-p`，没有 `EnvironmentFile`/`Environment` 注入新 exporter 的 `EXPORTER_NODE_ID`、`EXPORTER_ENV`、`ACCOUNTS_BASE_URL`、`INTERNAL_SERVICE_TOKEN`、`SNAPSHOT_STORE_PATH` 等契约变量。
- `deploy_xray_exporter.yml` 顶层虽计算了若干同名 Ansible 变量，但 role template 没有消费这些变量，不能把“playbook 变量已计算”当成“systemd 运行时已配置”。
- control-plane `xray-exporter` `origin/main` 目前只有 `/v1/snapshots/latest`；`/v1/snapshots/window` 在 `codex/multi-node-billing-ingestion` 分支，尚未形成 main 发布物。
- GitOps `compose/web-saas/docker-compose.yml` 把 Billing source 指向 `http://127.0.0.1:8080`。Billing 在 bridge 网络容器内运行，该地址指向 Billing 容器自己，不是宿主机 exporter。

## 最小修复建议（只作为后续 PR 设计，不在本任务直接改 UAT）

1. 先发布包含 window API 的 exporter 构建，并在 role 中以 commit/digest 或内部 artifact 方式固定来源；删除“外部旧 release 与新 control-plane 契约并存”的歧义。
2. 将 node/env/token/accounts URL/history path 作为每个 exporter instance 的显式变量，渲染到 root-only environment file；两个实例必须使用不同 history SQLite path。
3. 为每个独立累计计数面配置稳定、唯一的 `EXPORTER_NODE_ID`；所有实例显式 `EXPORTER_ENV=uat`，禁止依赖 exporter 默认 `prod`。
4. 为 Billing 提供可达的内网 exporter 地址（service DNS、受控 reverse proxy 或明确 host-gateway）；禁止跨容器使用 `127.0.0.1`。source JSON 必须为每个 source 声明 `source_id`、`base_url`、`expected_node_id`、`expected_env=uat`。

## 必须加入的测试/探针

- role/template 静态测试：渲染两实例后，检查 node id、env、token、accounts URL、独立 SQLite path 均出现，且 systemd `ExecStart` 不再依赖未消费的旧参数。
- exporter HTTP contract test：Bearer 访问 `/v1/snapshots/window`，验证分页、`node_id/env`、同 UUID 多 inbound、错误 token 拒绝。
- compose 网络测试：从 Billing 容器命名空间访问每个 source；禁止只在宿主机执行 curl 就宣称链路可用。
- UAT 只读验收：验证 exporter health/window、Billing source watermark、Accounts usage summary 与 Portal monthly quota；不连生产 endpoint，不修改 UAT PostgreSQL。

## 不应在本仓直接做的事

- 不直接编辑 GitOps `.env.uat` 或 UAT 主机运行时配置。
- 不在生产 `svc.plus` 上替换 exporter 或重启服务。
- 不执行 UAT 数据库迁移、清理或账务修正。
