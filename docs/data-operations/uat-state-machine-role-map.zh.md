# UAT 数据状态机：角色映射与执行边界

设计依据：用户提供的本地 `knowledge/content/02-iac-devops/cloud-infrastructure-devsecops-baseline/12-uat-database-sync-and-prod-promotion-state-machine.zh.md`。本次审阅版本的 SHA-256 为 `75ce682f47dc47bce1c9941779c43dc4edf7e3fae220e27ce5478c5842162c16`。

截至本次核验，该文件尚未进入 `knowledge/main`，因此不提供失效的公开链接，也不擅自提交其原文。该文档是目标设计，不是现成运行证据或 PROD 执行授权。

## 三条独立数据流

1. PROD Supabase → PROD Selfhost：显式生产单向同步，频率、冲突规则和审批尚需确定；普通发布不启动此链路。
2. 经授权的一次性 PROD Supabase → UAT Supabase → UAT Selfhost 初始化：来源、脱敏、allowlist 和基线须可追溯；初始化后冻结，不在每次发布覆盖。现有显式 `legacy_import` 与本次角色组件相互独立，不证明上述两跳已完成。
3. UAT Selfhost 演练 → 人工审批 → PROD 原位升级：只晋级通过验收的同一制品，不向 PROD 复制 UAT 数据。Supabase 应急恢复是单独人工旁路。

## 实现分工

| 状态／组件 | Playbooks 角色 | 当前边界 |
| --- | --- | --- |
| S0–S3 来源、审批、冻结 | `web_saas_data_baseline` | 校验并保存不可覆盖的 manifest 元数据；`manifest_validated` 不等于审批认证、真实数据复制或数据库冻结完成 |
| S1 数据库预检 | `web_saas_data_preflight` | 精确 clean 版本，非空用户及订阅，来源／baseline／授权样本引用；不执行登录或权益验收 |
| S4 加密备份 | `web_saas_data_backup` | 只读主库；要求独立挂载的 `/data`，目录 0700、档案 0600；密钥从环境读取，直接流式加密，不生成明文 dump |
| S4 隔离恢复 | `web_saas_data_restore_verify` | 在唯一 `release_verify_<run-id>` 库真实恢复；校验 checksum、schema、完整表行与序列状态；只清理本次创建且 OID 未变的临时库 |
| S5 增量迁移 | `web_saas_data_migration` | 验证原位目标和证据契约后明确阻断；现有 Accounts 官方命令缺少受控目标版本／checksum／锁超时接口，不能执行任意 SQL 代替 |
| S6–S11 G1/G2/G3、回滚、同 digest 再升级 | 发布控制面 + 后续业务验收适配器 | 尚未注册完整执行能力；数据库组件成功不能自动标记为可晋级 |

兼容入口 `web_saas_release_upgrade` 已成为组合角色，顺序调用 preflight → backup → restore_verify；没有隐藏的初始化、复制、down migration 或应用发布。

统一 owner playbook 是 `web-saas-data-operations.yml`，按显式 operation 调用角色。缺少环境／精确 host 时不匹配任何主机。baseline 和 migration 可作本地契约验证；migration 在合法输入下仍失败，不能当作已实现的迁移执行器。

## 从控制面调用

唯一公开入口仍是 Toolkit 的 `environment-data-operations.yml`。选择：

- `environment=uat`；`mode=preflight` 或 `backup`。
- `config_json.execution_path=selfhost_roles`，调用固定 SHA 的 owner `selfhost-data-lifecycle.yml`（仅 `workflow_call`）。
- `caller_run_id` 必须属于已审核的 Selfhost orchestrator main／tag dispatch，且运行成功或仍在执行；只使用它生成的环境 CMDB，精确 `web-saas-uat`，不手工录入 IP 或用户。
- `release_tag` 使用 Accounts 不可变 tag，`expected_schema_version` 精确指定；源码 commit 与 tag 必须一致。
- preflight 缺少来源、baseline、授权订阅样本时由角色阻断。backup 额外要求 `confirm_backup=true` 和上述非敏感引用。引用字符串不是签名或业务验收证据。

backup 密钥运行时读取 Vault `kv/uat/backups` 的 `web_saas_release_passphrase`，至少 32 字符；GitHub Secret、dispatch input、extra-vars 文件、receipt 或公开日志都不保存它。未配置则停止，不生成占位密钥。SSH 仍使用现有环境部署凭据；owner workflow 的精确 SHA 必须在 UAT JWT 白名单中。

`request_sha256` 只绑定本次组件请求、tag、源码与基线版本，不是接受过 UAT 业务验收的镜像 candidate。公开 artifact 仅包含经过 allowlist 过滤的组件结果，始终 `business_acceptance=false`；私有 receipt、明文数据、用户标识和密钥不上传。

## 验证与阻断

离线测试覆盖来源／样本缺失、非空订阅、dirty／精确版本、备份与恢复契约、禁止 PROD 和任意执行输入。CI PostgreSQL 17 另执行真实加密／恢复脚本，证明同 row count 的数据变化、dirty、空订阅、已有临时库均被阻断。CI 只使用一次性服务和 fixture，不证明实际 `/data` 卷、G1/G2/G3 或 UAT 发布成功。

schema hash 只在用于比较的流中去除 pg_dump 随机的 `\restrict` / `\unrestrict` 标记，实际备份不改写。完整行和序列数据只进入哈希流，不进入日志／明文文件。参见 [PostgreSQL pg_dump 文档](https://www.postgresql.org/docs/17/app-pgdump.html)。并发数据变动会使恢复核验失败，必须调查或安排审核后的写入隔离，不能自动重试。

当前已观察到的 Selfhost 基线是 `2026092801 / dirty=false / users=2 / subscriptions=0`；另一条 Supabase 只读路径为 `2026092703 / dirty=false`。二者不能混为同一数据集或相互替代验收。无非空授权订阅样本、真实旧密码／权限／权益验证和独立 `/data` 持久性证据时，禁止迁移、整套演练和 PROD 晋级。

后续需要：可信 manifest／receipt producer、受控 Accounts 原位 migrator、G1/G2/G3 专用测试账户与验收适配器、旧／新同 digest 回滚与再晋级证据。PROD 覆盖恢复始终另行审批，不自动执行。
