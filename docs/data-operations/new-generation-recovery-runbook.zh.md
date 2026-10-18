# 新代际主机快照恢复演练

适用于 V2 → V3、V3 → V4 等代际。版本来自 GitOps manifest，执行器不把 v2/v3 固定为业务分支。旧主机作为只读快照源；新主机、独立数据盘和本地恢复库是写入目标。公共域名切换与独立 Vault Raft 恢复不包含在本演练中。

## 声明与执行边界

- IaC 创建持久 VM、保留的数据盘和静态 IP，并提供 CMDB/inventory。
- GitOps 声明恢复源 checksum、精确 schema、业务样本、固定镜像 digest 和两阶段 Doco-CD target。
- Playbooks 的 web_saas_generation_recovery Role 执行主机准备、归档 staging、恢复与验收。
- Toolkit 保留入口和证据放行；本次应用演练直接使用 SSH/Playbooks/Doco-CD，没有 dispatch CI 部署。

既有 web_saas_cold_recovery 的 restore_primary 仍然失败关闭。新 Role 只接受 IaC inventory 中明确标记 web_saas_recovery 的新代际，核对实际 hostname、资源名称、独立磁盘路径与 UUID。它拒绝源主机、普通 prod profile、已有但未记录的数据库，以及不同快照/磁盘的历史执行记录。

## 五阶段

1. preflight：校验清洁 GitOps checkout、不可变 tag/SHA、实际主机和磁盘。
2. prepare：配置恢复专用凭据；验证完整 Compose；Doco-CD 只启动 storage target。
3. restore：传入 0600 的控制机归档；核验 checksum 后只在不存在 account 库的新实例创建目标库。使用事务恢复，不使用 clean/CASCADE 或覆盖现有库。记录迁移状态、样本、数据库身份、全表/序列指纹。
4. deploy：先重新核验恢复 checkpoint，再让 Doco-CD 选择同一 tag 的 application target。
5. verify：验证 Accounts/Billing HTTPS readyz、Console 未登录跳转、写请求拦截、内部网络隔离，以及启动后的完整数据/序列指纹。

中断时保留目标。没有完整 checkpoint 的已有 account 库不能自动重试覆盖；完整恢复后再次执行 restore 只核验原 checkpoint，不重复 SQL。

若第一次事务恢复失败，只能在核实没有 checkpoint、没有用户关系、没有活动连接后，显式提供观测到的 OID 调用 quarantine_empty_failure；它将空库重命名为 account_failed_<OID> 并保留证据，不删除或覆盖原库。正常 restore 不会自动调用它。

归档目录若没有 CREATE SCHEMA public，保留新库默认 public；只有归档明确声明该 schema 时才移除新建库的空 public。非强制 RLS 表的属主映射到隔离应用角色；强制 RLS 或已有超级用户/BYPASSRLS 角色会被拒绝，绝不通过关闭 RLS 或增加全局权限绕过验证。

## 隔离与凭据

恢复归档沿用既有 PostgreSQL custom-format 文件，本记录不声称该 DB 文件已加密。源文件经经过主机密钥校验的 SSH 读取；控制机临时目录 0700、文件 0600，验收后删除临时副本。目标归档与证据保留在独立 /data，目录 0700、文件 0600。

恢复凭据从 Vault sys/tools/random 取得独立熵并只写入目标主机私有文件，不复制源 SMTP、Stripe、OAuth 或会话签名值，也不修改生产 KV。重新初始化另一台新恢复主机可生成新的凭据；已有目标重跑保留本机凭据。

私有镜像认证引用 kv/CICD 的 GHCR_USERNAME 与 GHCR_TOKEN。密码仅通过 docker login --password-stdin 传递；目标 /root/.docker/config.json 为运行时凭据，由控制器只读挂载。临时凭据文件在验收后删除，公开记录只保留路径和键名。

应用固定使用已发布镜像。DATABASE_BACKGROUND_WRITERS=false，应用数据库角色 default_transaction_read_only=on。内部 Docker 网络阻止应用外联；Caddy 入口只发布在目标 loopback，使用本次恢复的独立本地 CA，拒绝非探针 Accounts/Billing 路由和写请求。V3 别名只写入 V3 的 hosts，不代表公共 DNS 已切换。

这组验收证明归档恢复与核心服务可启动，不替代原密码登录、真实支付、终端代理或全量生产切换验收。Stunnel、外部邮件/支付/计量及共享 Observability 没有在此隔离拓扑中重新 bootstrap。

## 执行方式

常规方式使用 web-saas-generation-recovery.yml 与原始 IaC artifact 的动态 inventory。目标变量由受控运行文件提供，敏感值不得进入命令行。

跨境逐任务 SSH 延迟较大时，可在目标已经完成 Vault 凭据准备后，使用 scripts/pipeline/run-generation-recovery-local.sh，通过一次 SSH 在目标本机执行同一固定 Playbooks commit。输入仍为原 IaC inventory、固定 GitOps tag/SHA 与 runtime.yaml；ansible_connection=local 只改变执行位置，主机和磁盘检查保持有效。该助手不得用于生产源主机。

每次保存：Playbooks commit、GitOps tag/SHA、实际 image digest、原始归档 checksum、迁移状态、样本和指纹结果、V2 容器身份/启动时间/重启数对比、公共域名对比。公开文档只记录非敏感结果，不保存凭据、DSN、用户行或备份内容。

## 2026-10-18：V2 → V3 实测

- 主机 web-saas-prod-v3：持久 GCP VM、20GB 系统盘、50GB 独立 /data；V2 为只读源。
- GitOps tag：prod-v3-recovery-2026.10.18-r2，commit 2c1f580599f4815dbdfa824a836fb748427380a1。
- 恢复/应用验收执行版本：Playbooks cbde19aae5681e6fcfbdb869de3eec0a0c97cc82。后续提交仅补充测试与记录时，不倒述为已执行版本。
- 原归档 SHA-256：84db2c11bf3d7de163ba5b43f332ce86e9d4e8ad56e9534a91546f1a53070e2d。
- schema 2026101703，dirty=false；用户 30、subscription_records 12、billing_ledger 12218。
- 启动前后全表/序列 SHA-256 均为 014adffaea602cc03363325b59f0ea97b48ba8f7704602bc903d34d42e508072。
- Accounts/Billing HTTPS readyz 均为 200；Console panel 为 307；写请求 403；应用网络 internal=true。
- 应用角色 rolsuper=false、rolbypassrls=false、默认事务只读；12 张 RLS 表保持启用，没有强制 RLS 被关闭。
- 重复 restore 返回 verified，Ansible changed=0、failed=0，未重复 SQL。
- V2 五个核心容器 ID、StartedAt、RestartCount 前后一致，重启数均为 0；三个公共域名仍指向原 V2 地址。
- V3 Accounts 曾因 RLS 属主问题重启 8 次，修复后连续观测保持 8；不将这段历史描述为零重启。首次空库失败保留为 account_failed_16384。

演练修复了基础 Compose 继承变量缺失、私有 registry 认证、PostgreSQL 目录 UID/GID、历史归档 public schema 与 RLS 属主映射问题。所有修复只作用于新 V3；没有修改 SaaS 镜像或业务特性。

源备份保持不变。V3 保留 /data/backups/web-saas/prod-v3/<archive-sha>/account.dump，以及 /data/recovery/prod-v3 下的 prepared、quarantined、restored、rls-ownership、application-acceptance 证据。控制机与 V3 用户目录中的归档/registry 临时副本在完成验收后清理。

这次没有执行 Vault Raft 独立实例恢复、公共 DNS 切换、旧密码登录或真实支付；V3 仅提供 SSH 隧道可达的 loopback 8443 只读验证入口。主机本机已配置四个 V3 别名，客户端可通过 SSH 隧道、显式解析与恢复公开 CA 验证 HTTPS，勿将恢复 CA 安装到生产信任链。
