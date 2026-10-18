# web-saas PROD V2 冷备、容灾与跨机恢复技术白皮书

版本：2026-10-18
适用对象：`web-saas-prod-v2`、selfhost PostgreSQL、Vault 快照及独立恢复主机
安全等级：公开技术说明；不包含密码、Token、私钥、DSN、用户标识或备份内容

## 1. 摘要与边界

本白皮书记录 PROD V2 在不增加新特性、不重启业务、不修改生产业务数据的前提下完成的全量快照、校验和隔离恢复验证，并把后续执行固化为可重复的 Playbooks Role。

这里的 `V2` 是本次快照的服务变体参数，不是 Role 的固定实现。`web_saas_cold_recovery_service_variant` 可以取 `v2`、`v3`、…、`vN`，也可以取现有命名形式 `prod-v2`、`prod-v3`；每次只替换目标变体、release tag、主机和对应 receipt，不复制一套新的备份/恢复脚本。

本次已完成：

- 记录域名、GitOps 构件、容器构件、核心非敏感配置、挂载关系和 Vault KV 路径清单。
- 在 `web-saas-prod-v2` 的独立 `/data` 块存储上保存数据库快照、运行时清单、Vault 原生加密快照和 SHA-256 清单。
- 在隔离 PostgreSQL 17 容器恢复数据库，核对迁移版本、用户数、订阅数和账本数量。
- 对 Vault Raft 快照执行解密回读与原生 `snapshot inspect` 完整性检查。
- 清理本地明文快照和临时恢复文件；没有把任何 secret value 写入本记录。

本次未完成且不能被探针结果替代：

- 没有创建 `web-saas-prod-v3`，也没有在共享 PROD Vault 上执行覆盖式恢复。
- Vault 仅完成快照解密、读取和 inspect；真正恢复到独立 Vault 实例需要独立目标、初始化/unseal 授权和单独审批。
- 没有执行真实 PROD 数据库回滚、真实支付、用户登录或终端代理验收。

## 2. 当前生产基线

### 2.1 主机、云区域与发布控制面

| 项目 | 事实 |
| --- | --- |
| 主机 | `web-saas-prod-v2` |
| GCP | 项目 `open-platform-prod`，区域 `asia-east1-a` |
| 服务变体 | 当前为 `prod-v2`；后续以 `web_saas_cold_recovery_service_variant` 灵活传入 `v3` … `vN` 或其他已批准命名 |
| 数据盘 | `/data`，与主机生命周期独立；PostgreSQL 实际数据目录 `/data/postgresql` |
| GitOps release | `prod-v2-2026.10.18-r5` |
| GitOps commit | `060dadeca933700254d9153632345f0fc8b402d7` |
| Doco-CD | 目标 `prod-v2`，轮询间隔 60 秒 |
| 发布路径 | SSH → Playbooks → GitOps → Doco-CD；本次没有 dispatch CI 部署 |

主机和 `/data` 数据盘均保留。判断 PostgreSQL 存储位置必须以容器的实际 `docker inspect` 挂载为准，不能因存在同名历史 Docker volume 就改变结论。

### 2.2 域名与共享域

- `console.svc.plus`
- `accounts.svc.plus`
- `billing.svc.plus`
- `console-selfhost-prod-v2.svc.plus`
- `accounts-selfhost-prod-v2.svc.plus`
- 共享域：`svc.plus`

域名/DNS 事实属于 GitOps/IaC 范围；本 Role 只消费已经核验的目标主机，不创建、删除或修改 DNS。

### 2.3 当前容器构件

核心构件以 tag 和不可变 digest 绑定。以下是本次已记录的可公开元数据；完整清单保存在快照的 `runtime/manifest.txt`，并由 `SHA256SUMS` 校验。

| 服务 | tag | digest |
| --- | --- | --- |
| Accounts | `sha-8c7ecd9c` | `sha256:6d70a8ff8319c0fc058fd8faaea212d74cd85a01bb7a00a1c1840be1e8fc5e7f` |
| Billing | `sha-8cdd4e95` | `sha256:ae7b56e2e01011f9f32b93a4106f1897eab71e857c7c9f2ecf676c95fdb65bbb` |
| Console | `sha-bef82c80` | `sha256:71827a3c453be8f935635ba0a458a847890176e340e3b72f7437d757fa8dff22` |
| PostgreSQL | `sha-7759f451` | `sha256:f97857bd59c1fc55e3be3942b2c8f5554a8e9ee1d08bf3e834940c33189a141c` |
| Caddy | `959d6c8` | `sha256:9895385033fea2bdb445841b45f37e6cd58b9e071a16e4c147e483abb74a0acf` |
| Content | `daily-build-2026.10.17-r2` | `sha256:42079743e3b6cd8297142cff84af85668342aa638aed30fef7090beeecff21ed` |
| Stunnel | `sha-7759f451` | 以 `runtime/manifest.txt` 为准 |
| OpenTelemetry | `0.141.0` | 以 `runtime/manifest.txt` 为准 |
| Doco-CD | 固定 digest | `sha256:46f3e44106fd0f4f30bc450682038509484d9aa28916edd6039b0af45bdc1cec` |

### 2.4 核心非敏感运行配置

以下只记录键和值，不记录任何 secret：

```text
DATABASE_BACKGROUND_WRITERS=true
BILLING_INGEST_MODE=push
DEFAULT_REGION=jp
XWORKMATE_SHARED_TENANT_DOMAIN=svc.plus
RUNTIME_HOSTNAME=console.svc.plus
```

应用镜像、GitOps commit、配置 checksum、挂载和运行服务状态共同组成发布基线；单独一个容器 tag 或 `/readyz` 不足以证明可恢复性。

## 3. 敏感信息目录清单

Vault 只记录路径清单，不在公开文档中记录 value。本次共发现 104 个 KV 路径。相关路径包括：

```text
kv/prod-v2/web-saas
kv/prod/accounts/runtime
kv/prod/accounts/oauth/github
kv/prod/accounts/oauth/google
kv/prod/billing-service
kv/prod/database-upgrade
kv/prod/platform/smtp/google
kv/CICD/domains/svc.plus
kv/CICD/prod/gcp-bootstrap/xworktech
kv/CICD/prod/iac_state
kv/prod/XConnect/PH/PH-XConnect.svc.plus
kv/prod/ulighthost-xconnect/observability.svc.plus
```

完整路径名保存在 `vault/vault-kv-paths.txt`。该文件也不能被当作凭据使用；恢复时必须重新通过 OIDC/Vault 授权读取运行时值。密码、Token、私钥、DSN、unseal key、recovery key 和明文快照禁止进入 Git、PR、日志、receipt 或本白皮书。

## 4. 本次全量快照证据

正式快照目录：

```text
/data/backups/web-saas/prod-v2/full-snapshot-20261018/20261017T173532Z/
```

目录权限为 `0700`，文件权限为 `0600`。内容如下：

| 路径 | 用途 | 安全边界 |
| --- | --- | --- |
| `db/account.dump` | PostgreSQL `account` custom-format 快照 | 本次实物是受文件权限保护的 custom-format 文件；该文件本身未宣称加密 |
| `db/globals.sql` | PostgreSQL 全局角色/权限恢复材料 | 不含运行时 secret value |
| `db/checkpoint.txt` | 版本、用户、订阅、账本等基线摘要 | 只含计数与版本 |
| `runtime/manifest.txt` | 域名、构件、挂载、配置 checksum 和运行事实 | 不含 secret value |
| `vault/vault-raft.snapshot.enc` | Vault 原生 Raft 加密快照 | 只允许受控 operator 流程读取 |
| `vault/vault-kv-paths.txt` | KV 路径名清单 | 不含 value |
| `vault/vault-raft.inspect.txt` | Vault 原生快照 inspect 输出 | 不含解密内容 |
| `SHA256SUMS` | 快照完整性清单 | 不含 secret value |

数据库快照最终 SHA-256：

```text
db/account.dump  84db2c11bf3d7de163ba5b43f332ce86e9d4e8ad56e9534a91546f1a53070e2d
db/globals.sql   0609e2562cb6b7de22c6d375ba077fa9f371e734f3e3ab3413d9915b2815eee9
```

Vault 加密快照 SHA-256：

```text
8d65db42689f4eb92431cf0b5cfb32f9d3f26ebd64a0abd9c783ef3759f7a2ff
```

本地加密副本只用于受控操作，解密 key 保存在本机 Keychain，不进入仓库、日志或文档。验证完成后本地明文快照和临时恢复文件已清理。

### 4.1 数据库恢复验证结果

快照在隔离 PostgreSQL 17 容器恢复成功：

- schema：`2026101703`
- 用户：30
- 订阅记录：12
- 快照账本记录：12,218
- `DB_RESTORE=PASS`

隔离容器使用独立 Docker 网络、无宿主机端口、无生产数据盘挂载和临时内存文件系统。恢复过程只读原始快照，不连接生产数据库，不覆盖生产库。

账本数量必须和快照 checkpoint 绑定解释。生产在快照之后仍可能有合法写入，因此不能用后来实时库的计数倒推快照内容等同于实时库。

### 4.2 Vault 快照验证结果

- Vault 服务：`https://vault.svc.plus`
- Vault 版本：`1.21.4`
- 使用原生 Raft snapshot inspect 通过完整性检查。
- 加密快照已解密回读，未把明文内容写入仓库或本记录。
- 没有在共享 PROD Vault 上执行 restore、覆盖或重新初始化。

## 5. 可重复的 Playbooks Role 设计

新增：`roles/web_saas_cold_recovery`，顶层显式入口：`web-saas-cold-recovery.yml`。

它组合现有 `web_saas_data_backup` 和 `web_saas_data_restore_verify`，不复制 PostgreSQL dump/restore 逻辑，并将表名、迁移版本列、dirty 列、排序列、用户表和订阅表参数化，以兼容历史模型和当前 Accounts/Billing 模型。

### 5.1 四个模式

| 模式 | 行为 | 允许覆盖生产吗 |
| --- | --- | --- |
| `backup` | 在源主机 `/data` 建立唯一、不可覆盖的加密数据库 checkpoint；仅把加密 archive 与非敏感 receipt 拉到控制机 | 否 |
| `stage` | 将加密 archive 传到独立目标的 `/data`，校验 SHA-256 和权限，不解密 | 否 |
| `restore_verify` | 目标 PostgreSQL 中创建本次 run 专属临时库，恢复并比对 schema、迁移状态、数据指纹和批准的计数 | 否 |
| `restore_primary` | 永久失败并提示走 operator-only 手册 | 否，故意 fail closed |

运行要求：

- `web_saas_cold_recovery_run_id` 必须唯一且为数字；不能复用 checkpoint 目录。
- `web_saas_cold_recovery_service_variant` 是显式参数；它会写入非敏感 receipt，并在跨机 stage/restore 时防止把 V2 快照误用于 V3 或其他变体。
- `/data` 必须是真实挂载点，备份不能落到 root filesystem 或 symlink。
- archive 使用 AES-256-CBC + PBKDF2 加密，passphrase 仅运行时经 OIDC/Vault 注入。
- archive `0600`、父目录 `0700`；传输只传加密文件，不产生明文 dump。
- receipt 只含版本、计数、checksum、表名、恢复状态等非敏感证据。
- 跨机恢复不比较 PostgreSQL system identifier；它应当在新实例上不同。源 system identifier 仍作为 receipt 事实保留。
- 不清除 `dirty`，不执行 down migration，不重建生产表，不删除用户、订阅、付款或用量账本。

### 5.2 当前 PROD V2 模型参数

针对本次实际 Accounts/Billing 数据模型，执行者需要通过受控变量传入并审核：

```text
web_saas_cold_recovery_migration_table=accounts_controlled_migration_history
web_saas_cold_recovery_migration_version_column=to_version
web_saas_cold_recovery_migration_dirty_column=
web_saas_cold_recovery_migration_order_column=applied_at
web_saas_cold_recovery_expected_migration_rows=3
web_saas_cold_recovery_users_table=users
web_saas_cold_recovery_subscription_table=subscription_records
```

空 dirty 列表示该审计表没有 dirty 字段，receipt 使用 `not-applicable`；它不等同于忽略迁移状态。迁移记录数量、目标版本、checksum 和应用验收仍必须精确匹配。

### 5.3 非敏感示例

以下仅展示结构，路径、主机组和 passphrase 必须由受控执行环境提供：

```bash
ansible-playbook web-saas-cold-recovery.yml \
  -i /path/to/iac-generated-cmdb-inventory.yml \
  -e web_saas_cold_recovery_environment=uat \
  -e web_saas_cold_recovery_service_variant=prod-v2 \
  -e web_saas_cold_recovery_release_tag=restore-drill-20261018 \
  -e web_saas_cold_recovery_run_id=2026101801 \
  -e web_saas_cold_recovery_expected_version=2026101703 \
  -e web_saas_cold_recovery_controller_archive_dir=/protected/backup-transfer \
  -e web_saas_cold_recovery_receipt_file=/protected/backup-transfer/receipt.json \
  -e web_saas_cold_recovery_source_database_id=approved-source-id \
  -e web_saas_cold_recovery_baseline_id=approved-baseline-id \
  -e web_saas_cold_recovery_authorized_subscription_sample_id=approved-sample-id
```

真实 passphrase 不得写入命令行历史、GitHub Variables、inventory、文档或日志；应由 Toolkit 的 OIDC/Vault 授权在运行时注入。

## 6. 冷备与跨机容灾流程

```text
发布/灾备审批
  ↓
源主机只读预检：/data、PostgreSQL、迁移状态、运行构件、Vault 路径
  ↓
backup：新建唯一 checkpoint + 加密 archive + receipt + fsync
  ↓
SHA-256 校验并传输加密 archive 到独立目标
  ↓
stage：目标 /data 目录和文件权限校验
  ↓
restore_verify：隔离 PostgreSQL 恢复、schema/数据/计数/迁移状态验证
  ↓
人工验收：旧账号登录、订阅权益、额度、账本和健康检查
  ↓
如需切换：另行审批，由 operator-only 恢复手册执行，不由通用 Role 覆盖生产
```

Vault 灾备独立于数据库恢复：

1. 使用 `roles/vhosts/vault/files/vault_operator_data.sh snapshot-save` 产生原生 Raft snapshot。
2. 立即执行 `snapshot-inspect`，保存只含元数据的 inspect 证据。
3. 在受控工作站加密归档；只把快照 checksum 和路径清单写入发布证据。
4. 恢复必须指向独立 Vault 实例，核对 cluster identity、Raft 状态和 unseal 授权。
5. 禁止把共享 PROD Vault 的覆盖式 restore 作为自动回滚动作。

## 7. 回滚边界与数据安全

- 备份失败、checksum 不匹配、权限不符合、迁移版本不精确或隔离恢复失败：停止，不进入真实升级。
- 应用失败：优先回到兼容当前 schema 的上一个 digest；保留向前兼容新增字段/表。
- 数据库问题：先隔离恢复核对，再制定前向修复；不自动执行破坏性 down migration。
- 生产数据损坏：只能先恢复到隔离实例、比较差异并重新审批；不能自动覆盖生产，避免丢失快照之后的新交易。
- 任何业务验收失败都停止晋级，不通过重启或重新 bootstrap 掩盖问题。
- 本 Role 不创建/销毁云资源、不改 DNS、不写 CMDB、不 bootstrap Vault/Observability；这些边界分别由 IaC、GitOps、Toolkit 和既有 operator 流程负责。

## 8. 验收清单

每次运行生成不含敏感值的证据，至少绑定：

```text
environment
release_tag
source host / destination host identity
source GitOps commit and image digests
database migration table/version/row count/dirty state
schema_sha256
data_sha256
archive_sha256
Vault snapshot SHA-256 and inspect status
backup directory and file modes
restore database name and isolated restore result
operator approval / previous compatible digest
```

最低验收项：

- 归档存在且不可覆盖，`SHA256SUMS` 通过。
- PostgreSQL schema 精确为目标版本，状态 clean 或明确 `not-applicable`。
- 旧账号登录、权限、订阅权益、额度和账本保留；真实用户属性不被测试修改。
- 隔离目标 `/readyz` 通过，且临时数据库和临时容器在证据确认后清理。
- Vault 快照 inspect 通过；没有把明文快照或 secret value 写入日志。
- 缺少备份恢复证据、版本不匹配、迁移脏状态或业务验收失败，均停止晋级。

## 9. 快照创建时的结论

快照创建时，PROD V2 已具备可恢复快照、DB 隔离恢复证据和 Vault 快照完整性证据；生产业务没有因快照而重启、改动或丢失数据。当时 `web-saas-prod-v3` 尚不存在，且尚未取得独立 Vault unseal/restore 授权，不能宣称已完成独立 Vault 恢复或跨机生产切换。

后续演练应在 UAT 或新建的独立恢复主机完成完整 `backup → stage → restore_verify`，并以同一 release receipt 绑定证据；确认通过后再单独设计生产切换，不把恢复覆盖动作塞入普通发布流水线。

## 10. 2026-10-18 新代际恢复演练

已在新建持久主机 web-saas-prod-v3 完成 DB 恢复和核心应用只读验收，20GB 系统盘、50GB 独立数据盘均实际使用。公共域名继续指向 V2；V2 五个核心容器身份、启动时间与重启计数保持一致。恢复后为 2026101703 / dirty=false，30 用户、12 条订阅、12218 条账本；应用启动后全表/序列指纹未改变，重复执行恢复 changed=0。

完整执行约束、不可变 tag/SHA、修复过程、保留的失败空库、凭据临时副本清理和验收边界见[新代际恢复 Runbook](new-generation-recovery-runbook.zh.md)。本次未恢复共享 Vault，也未切换公共域名或进行真实付款。
