# 代际升级与数据追平证据

发布链：**SSH → Playbooks → GitOps → Doco-CD**。不 dispatch CI 部署。
源/目标代际显式选择；数据库操作在目标执行，源仅接收只读数据库请求。

每次证据绑定：

`源/目标代际 + release tag + source SHA + image digest + SQL checksum + 迁移前后版本 + 备份/恢复结果 + 数据指纹 + 验收结果 + 回退 digest`

## 执行顺序

1. 从 GitOps 与 IaC CMDB 核对唯一目标，不写入第二套拓扑；记录每个服务的候选
   source SHA、固定 OCI manifest/index digest 与兼容目标 schema 的回退 digest。
2. 按维护窗口排空并暂停目标写入端及 Doco-CD；核对所有后台/外部写入端。
   不修改源主机、不切公共 DNS、不重新 bootstrap 共享 Vault/Observability。
3. 调用 `web-saas-generation-usage-reconciliation.yml`，复用已有加密备份和隔离
   恢复 Role。校验本次 archive、schema/data 指纹、数据库身份、精确版本和 dirty。
4. 默认只读预览；显式启用 apply 后，重新计算并校验 plan SHA，在单事务中合并。
   同主键账务内容不同即停止；目标独立账务不得覆盖，quota 不得隐式补满。
5. 在目标备份目录保存 `reconciliation-receipt.json`。它只是数据阶段证据，
   不是部署成功凭证。声明的候选构件不等于已观察的运行构件。
6. 通过 GitOps 不可变 release 选择已构建的 digest，由 Playbooks 恢复/选择已有
   Doco-CD。另存对账 tag/SHA、实际运行 digest、schema 与 readyz 结果。
7. 单独执行原密码登录、权限、额度、断流恢复、订阅和账本验收；支付限 Sandbox。
   将应用对账及业务验收证据与数据 receipt 一并归档，缺项保持 pending/blocked。
8. 正式入口切换需要另行审批。观察期保留旧代际与独立备份；不能由本入口自动切流。

## 证据与回退边界

- `web_saas_usage_reconciliation_components` 要求 accounts/billing/console 三个
  键；每项含完整 `source_sha`、`image_digest`、`rollback_digest`。
- `web_saas_usage_reconciliation_gitops_sha` 为完整 SHA；SQL checksum 为审核后的
  同批 SQL 内容哈希。纯数据追平不修改 schema，列表为空并记录版本前后相等。
- 每次使用新 run ID。目录 0700、文件 0600；不公开凭据、行数据、用户级标识。
- 迁移采用扩展兼容 → 应用切换 → 后续独立收缩；不能将不兼容旧应用作为回退构件。
- 事务失败不继续发布；应用失败优先回退兼容 digest。提交后的数据库修复优先向前，
  禁止自动 down migration、强制清 dirty 或覆盖主库。
- 两代独立数据库不是双主。源持续写入时只保证 snapshot 边界；最终切流需要
  写入冻结或已验证的增量复制机制，不能宣称永久实时一致。
- 冷备 Role 的 `restore_primary` 继续永久拒绝。严格只读源的演练不能直接运行
  在源主机创建文件的 backup 模式；复用已有快照或在目标接收只读导出。

当前新增 Role 的自动化验证与实际线上演练分开记录；不能把本文件当作已完成演练的证明。
