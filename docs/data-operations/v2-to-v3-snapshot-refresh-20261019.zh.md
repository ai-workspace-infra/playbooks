# V2 → V3 快照恢复、切库与平滑升级验收

日期：2026-10-19（北京时间）。执行路径：SSH → Playbooks → 已固定 GitOps → Doco-CD。
没有 dispatch GitHub Actions 部署；PR 自动检查仅作代码验证。

## 范围与边界

- 源代际 `prod-v2` 继续服务。只通过已认证隧道执行数据库只读事务，未在源主机安装、配置、写文件、停服务或重启。
- 目标为既有 `web-saas-prod-v3`。保留主机、20 GB 系统盘、50 GB 独立 `/data` 数据盘、PostgreSQL、Docker、SSH、证书、必要配置和备份。
- 不切换 `console.svc.plus`、`accounts.svc.plus`、`billing.svc.plus` 等公共域名。
- 本次是经用户明确授权的目标代际刷新，不是普通追加合并；旧 V3 独有测试数据不进入新业务库，但旧库和加密备份仍保留，不删除历史检查点。

## 已实际执行的顺序

1. 通过 V3 限定的 writer Role 停止 Doco-CD、Accounts、Billing、Console、Caddy；PostgreSQL 保持运行。
2. 对旧 V3 `account` 创建加密备份，并实际恢复到隔离数据库，版本、数据与结构指纹一致。
3. 在 V3 使用 PostgreSQL 17 客户端读取 V2 的导出快照。指纹查询与 `pg_dump` 使用同一个 REPEATABLE READ READ ONLY 快照；归档流直接加密落到 V3，不生成明文归档。
4. 恢复到新建且 OID 绑定的 `account_candidate_20261019170007`；全量业务行指纹与源快照一致。
5. 对候选库执行两段审核过的 `migratectl controlled-migrate`。每段前单独加密备份、实际隔离恢复并核对数据。旧 V3 业务库此时未被迁移。
6. 核对九个既有 V3 Stripe Sandbox 价格的 `livemode=false`、启用状态、币种、金额及周期；候选目录不沿用 V2 的 live 价格映射。未创建付款、退款或测试用户。
7. 按旧 V3 的对象级权限恢复非超级用户 `recovery_app` 权限；不变更角色口令、权限等级或全局默认授权。
8. 在一个事务中将旧 `account` 改名为 `account_before_20261019170007`，候选库改名为 `account`；核对新旧数据库 OID。
9. 恢复 V3 应用及 Doco-CD，核对固定镜像、数据库版本、数据指纹、HTTP 健康及 Doco-CD 实际观察到的 tag/commit。

源快照时间：**2026-10-19 17:02:04 北京时间**。源持续写入，因此以下数量是快照边界，不宣称永久零延迟：

| 数据 | 源快照及恢复后数量 |
| --- | ---: |
| 用户 | 33 |
| `subscription_records` | 13 |
| `billing_ledger` | 32,847 |

用户全行（包含原有密码散列、代理 UUID）、订阅、配额、分钟流量桶和财务记录均参与指纹核对；未公开行内容或用户级标识。
序列不属于 MVCC 行数据，保留 `pg_dump` 观察到的序列值；未调用源 `nextval`，不将序列值表述为同一 MVCC 快照指纹。

## 版本、归档与证据

- 执行相关 owner 提交：`2a9a1078d768d9174828d085a5a8c8e056cf7dbc`；Doco-CD 增补验收提交 `9567f02`。
- GitOps tag：`prod-v3-billing-catalog-2026.10.19-r2`。
- GitOps SHA：`cc72b7b4275530ec11c9bf1adfe0ffdca23b9f5a`。
- Doco-CD target：`prod-v3-stripe-sandbox`。本次沿用已固定应用构件，不重建镜像。
- `migratectl` 二进制 SHA-256：`5c74aa3c5bdb7268c1183ebbf1058b1ec33835ebec677e167055fe9ecdc6b4f0`。
- 原 V3 备份 SHA-256：`2367254f4a8447a2d4293b482e0476525978905d5061c9d4f72fc178860c6e60`。
- V2 加密归档 SHA-256：`d1c12a4a66ca9a892399eaa210e96f0e99d553adc4a168a682d4b66fb07a38df`。
- 排除审核目录及迁移元数据后的受保护业务指纹集合 SHA-256：`377b8ada1f1b12d3b31078b5507c3d41b4a3de9049ac7008bad8a67a40aec357`。

V3 归档及各阶段 receipt 目录：

`/data/backups/web-saas/prod-v3/prod-v3-billing-catalog-2026.10.19-r2/20261019170007/`

目录 `0700`，归档与 receipt `0600`。密钥仅在 Vault `kv/prod-v3/backups` 的 `web_saas_release_passphrase`，不写入公开记录。每段迁移前的归档、恢复验证库及旧业务库均保留。

| 起始版本 | 目标版本 | 审核 SQL SHA-256 |
| --- | --- | --- |
| 2026101703 | 2026101901 | `b9fee298512e7d888fe619917c8238120f0e227f4ea348e59397d074c41f878d` |
| 2026101901 | 2026101902 | `53f9aaf7c56a14fd7c132c6649b79582cd2063f8903b94efb4e23cbf4895437c` |

最终版本精确为 **2026101902，dirty=false**。两段增量 SQL 仅更新套餐目录；用户、订阅、配额和流量/财务账本指纹均保持源快照值。

| 服务 | source SHA | 固定 digest（亦为本轮应用回退构件） |
| --- | --- | --- |
| Accounts | `883e18216e1170e1fbab6932af320a402eafe082` | `sha256:7c1ec4a1bd246b4d18046e20a8792c413bf72f824b635766087bf8d6339c6ffc` |
| Billing | `22cbfc06d3fe030c2d32fa076335853fa6aa2865` | `sha256:462a7835f649ffad3bd88cb7e51e8e6ea4deb1f8fa6509f6a5ed5d9259058db4` |
| Console | `121569cda404c6a9a9bc6e1784f2f8c37f0c82b8` | `sha256:9fc0fb54fb680d553fbce23bff1ca1bebba60b070d039ff76ce43d3a035f15b4` |

## 已观察到的验收与未覆盖项

- V3 Accounts/Billing `/readyz` 均 200，Console 未登录 `/panel` 为 307。
- 三个应用容器重启计数均 0；PostgreSQL 未停止或重建。
- 恢复应用后再次核对活跃数据库 OID、精确迁移版本和业务指纹，通过；旧 V3 数据库仍存在且 OID 匹配。
- Doco-CD 健康，`POLL_CONFIG` 的 repository/tag/target 匹配，启动后日志实际观察到上述完整 GitOps SHA 与 tag；不公开原始日志。
- 公共服务探测 Accounts 200、Console 未登录 307；本次未改公共 DNS 或 V2 服务。
- 本地安全契约测试、隔离 PostgreSQL 17 真实 SQL 迁移/恢复/原子改名测试和 Ansible 语法检查通过。PR 自动 gitleaks 等代码门禁通过；不代替现场验收。
- **未自动验证**：用户原密码登录、登录后页面操作、终端代理连接、真实扣款或退款。密码散列与用户身份数据保留不等于已经完成交互登录。

## 回退边界

失败即停止，不自动删除旧库、候选库、备份或执行 down migration。应用回退仅可使用兼容当前 schema 的固定 digest。
如需将业务连接切回 `account_before_20261019170007`，必须先停止 V3 写入、备份当前活跃库并检查恢复后新增交易；这是新的显式恢复决策，不能自动覆盖本轮之后的数据。

关联 owner PR：[Playbooks #692](https://github.com/ai-workspace-infra/playbooks/pull/692)。
