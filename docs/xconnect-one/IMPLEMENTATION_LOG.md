# XConnect-One Playbooks/Gateway 实施更新记录

状态日期：2026-08-28

本仓职责：Gateway Agent、静态迁移/shadow、事务 apply、readiness、Ansible 角色和回滚

## 运行与迁移边界

- XConnect-One v1 Gateway 只运行 Xray-core；不提供 sing-box runtime 或 fallback。
- shadow 是默认模式且不修改网络。runtime apply 与 accounts-only authority 是两个独立、
  都需要显式启用的决定。
- 静态变量 `xworkmate_bridge_distributed_vpn_clients` 仍是审查和回滚证据；当前没有删除、
  改写或从 Accounts 反向生成该变量。
- accounts-only 模式的动态 peer 只能来自 Accounts GatewaySnapshot；角色 CI 会拒绝业务
  task/template 在该模式重新读取静态客户端变量。

## 已完成并推送的批次

| 批次 | 远端分支 | SHA | 结果 |
|---|---|---|---|
| 01 | `codex/xconnect-batch-01-gateway-contract` | `41bcb7c` | Gateway 角色与 Xray-only 合同 |
| 02 | `codex/xconnect-batch-02-gateway-shadow` | `3491e3e` | shadow 投影、差异和只读边界 |
| 03 | `codex/xconnect-batch-03-gateway-agent-shadow` | `2c97306` | Gateway Agent 签名 snapshot shadow |
| 04 | `codex/xconnect-batch-04-static-import-shadow` | `c5bab23` | canonical 静态导入、receipt 和 diff 工具 |
| 05 | `codex/xconnect-batch-05-gateway-apply` | `e7d2c7d` | 事务 apply、journal、LKG、rollback 和旧服务安全接管 |
| 06 | `codex/xconnect-batch-06-rollout-gates` | `98c45b2` | Controller-signed accounts-only authorization 与 readiness 门禁 |

## Batch 05 apply 边界

- apply 对 WireGuard、Xray 和 nftables 使用 staged validate、commit、runtime readback、
  checkpoint/LKG 和失败回滚；任一步失败不推进 applied generation。
- 捕获并恢复既有 `wg-xwm`/`xray-wg-tproxy` 状态、端口所有权和服务状态；不会在错误
  恢复时无条件启动旧服务。
- WireGuard/relay/TLS seed 使用 root 保护文件，专用用户和权限在 mutation 前预检。
- quarantine/runtime fault 需要显式 operator 处理，不自动把已撤销或已轮换的 key
  从旧静态状态复活。

## Batch 06 readiness 边界

- `xconnect-cutover-readiness --accounts-only` 验证 import receipt/baseline、Accounts/static/
  snapshot 集合、GatewaySnapshot 签名、policy digest、reconcile、successful apply、
  checkpoint/runtime readback 和连续健康样本。
- Controller approval 是单独 Ed25519-signed authorization，绑定 node/network/generation/
  snapshot、baseline、projection、policy、reconcile、mode 和 validity window；本地 JSON
  boolean 不能伪造授权，缺 pinned public key 时 fail-closed。
- Accounts Batch 09 (`98edbbe`) 已完成对应 producer 代码和自动化验证。生产仍需要
  endpoint 部署、根保护公钥分发、私钥/key rotation 运维和真实 HTTPS 集成证据。
- 任一 evidence 缺失时拒绝 accounts-only 并保持 shadow。回 shadow/LKG 是显式操作，
  不等于恢复旧凭据或静态 peer。

## 验证证据

- Gateway Agent/rollout gate 通过 Go test/race/vet、fuzz smoke、签名/篡改/重放门禁、
  TLS mock controller、policy/snapshot/apply-result 和权限测试。
- 角色、Agent、apply、takeover、cutover 共六套 Ansible/script tests、ansible-lint、
  schema/workflow parse 和 Linux amd64/arm64 builds 通过。
- isolated network namespace test 在 Ubuntu CI 执行，在 macOS 安全 skip。
- 上述 mock 和 namespace 是可执行集成合同，不是已部署 Accounts/Gateway live E2E，
  也不是 production accounts-only 许可。

## 当前限制与发布状态

- Accounts Batch 09 producer 尚未部署到 staging 并与本仓 verifier 做真实密钥和 HTTPS
  联调；连续健康 soak 与 operator approval 尚不存在。
- 静态 import、shadow、apply、readback 和 fallback 尚未在同一真实 staging 环境形成一套
  可审计证据链，因此静态 `group_vars` 不得退役。
- 五平台终端与 Gateway 的 join/ACL/rotate/revoke/rollback live E2E 尚未完成。
- 当前批次保留在特性分支，未创建新的 PR；后续 PR base 为
  `codex/xconnect-overlay-productization`，创建前需再次取得用户确认。
