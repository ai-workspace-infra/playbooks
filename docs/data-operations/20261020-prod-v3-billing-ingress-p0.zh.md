# V3 Billing/Vector 采集入口 P0 修复验收

时间：2026-10-20（Asia/Shanghai）。仅修复 V3 公共 Billing 入口。

保持 Xray Exporter → Vector → Billing；未启用 exporter pull，未让 Agent 绕过 Vector。
共享 Token 权威来源保持 `kv/prod/accounts/runtime` → `INTERNAL_SERVICE_TOKEN`。
Accounts/Billing/Console 运行态值与 canonical Vault 值内存比较均一致。

## 变更与执行

- PR：[Playbooks #699](https://github.com/ai-workspace-infra/playbooks/pull/699)。执行代码 `dac9b0f8`；后续 README 澄清提交 `cd85a32c`。
- 仅增加 `POST /v1/ingest/snapshots` 的反向代理，由 Billing 执行 Token 鉴权。
- 显式 `web_saas_public_ingress_activate=true` 在候选验证及配置备份之后，只重启 Caddy。
- IaC CMDB 限定 V3，SSH/IAP → Playbooks，未 dispatch CI 部署。
- 10:47:47 开始，10:49:52 完成，`failed=0`；固定现有 Caddy image 离线验证通过。
- Caddy 启动时间变为 `2026-10-20T02:49:52Z`；Accounts/Billing/Console/PostgreSQL 启动时间未变。DNS、应用镜像与 schema 未变。

## 运行验收

| 请求 | 结果 |
| --- | --- |
| 采集 POST 无 Token | 401 |
| 采集 POST 错误 Token | 401 |
| 采集 GET | 403 |
| 管理 status GET | 403 |

修复前账本 32,847 条，最新业务区间停在 10 月 19 日 17:03。
10 月 20 日 10:50:27 为 32,852 条，10:52:06 为 32,863 条；
`prod:agent-server-free-prod-v2-xhttp` 和 `prod:jp-xconnect.svc.plus-xhttp`
的新 checkpoint/分钟桶持续前进。没有发送有效伪造账务样本。

## 尚未完成

SG/PH 最新快照新鲜但 samples=0，尚无新增用户桶；US Free 和 `jp-xconnect-01`
的 Vector Token 正确，但 exporter 进程缺少有效 Token。本轮只读定位这些问题，未修改节点。
不能宣称全部节点已通过采集验收。

历史断档重放须检查 exporter 保留和 Vector buffer、counter reset、幂等及账务冲突。
入口恢复不证明旧分钟数据已补齐；不自动回灌 V2、不人工补账、不回退数据库。

详细升级时间线与历史索引由 Knowledge 维护，关联其 V2/V3 快照与升级复盘。
