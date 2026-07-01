# 迁移底层架构升级：基于 S3 对象存储的中转迁移方案

由于源机器的磁盘容量限制，我们之前的本地备份方案以及 Rsync 直推方案都可能面临各种不可预见的存储和网络瓶颈。结合从 Vault 中获取的 `ai-workspace-tfstate` S3 Bucket 凭证，我们重构了迁移流水线，彻底转向基于 S3 的中转架构。

## 方案概述

此方案修改原有的 Ansible 迁移脚本 (`site_migration` role)。所有的备份数据将流向 AWS S3 存储桶 `ai-workspace-tfstate`，不再使用本地的 `/var/backups/migration_export`，同时废除 Rsync 节点中转。将 S3 用于迁移数据的中转存储，并在迁移完成后自动在 S3 留存备份，这是一个长期更健壮的做法。

## 1. 凭证注入与环境准备 (Vault 动态 JWT 集成)

我们在 Ansible playbook 运行时，通过 `community.hashi_vault` 插件，使用 JWT 动态认证（而非写死静态文件），直接从 Vault (`https://vault.svc.plus`, 引擎 `kv`, 路径 `CICD`) 获取 AWS S3 相关的 AK/SK。这确保了零密钥落盘、零静态存储。

**前缀规则 (Prefix):** 
每次任务将在 S3 生成一个基于任务和时间命名的前缀：`<task-name>_<source>-<dest>_<date>`。 
例如：`site_migration_install.svc.plus-jp-xhttp-contabo.svc.plus_20260701`

---

## 2. 核心模块重构说明

### [Component: vhosts/gitea]
配置 Gitea 使其原生支持 S3 存储，从根本上解决大文件落盘导致磁盘耗尽的问题。

* **[NEW]** `playbooks/roles/vhosts/gitea/templates/app.ini.j2`
  创建 Gitea 的配置模板文件。
  注入 `[storage]` 和 `[storage.minio]` 配置块，配置 MINIO_ENDPOINT, MINIO_ACCESS_KEY_ID, MINIO_SECRET_ACCESS_KEY, MINIO_BUCKET 等 S3 参数。
  让 attachment, lfs, avatar, repo-archive, packages 统统走 S3。
* **[MODIFY]** `playbooks/roles/vhosts/gitea/tasks/main.yml`
  增加下发 `app.ini.j2` 模板到 `/etc/gitea/app.ini` 的 Ansible 任务。

---

### [Component: site_migration (Extract)]
提取阶段不再落地任何大文件（Tar/Gzip不写本地盘），全部管道化流式上云，支持断点续传。

* **[MODIFY]** `playbooks/roles/site_migration/tasks/extract.yml`
将迁移以下 6 大核心模块：

1. **pg_dump (PostgreSQL)**: 
   `pg_dump ... | gzip | aws s3 cp - s3://<prefix>/db/<name>.sql.gz`
2. **Gitea 数据同步**: 
   使用原生增量同步：`aws s3 sync /var/lib/gitea/data/ s3://<prefix>/gitea_data/`
3. **QMD PATH 同步**: 
   将 QMD 持久化数据目录同步：`aws s3 sync /path/to/qmd s3://<prefix>/qmd_data/`
4. **openclaw workspace 同步**: 
   工作空间数据上云：`aws s3 sync /home/ubuntu/.openclaw/workspace s3://<prefix>/openclaw_workspace/`
5. **Caddy 配置**: 
   配置作为备份上传：`tar -czf - -C /etc/caddy conf.d | aws s3 cp - s3://<prefix>/caddy_configs.tar.gz` (恢复时依然首选 ansible 重新渲染)
6. **容器镜像导出 (Docker Images)**: 
   抽取当前运行的所有/关键容器镜像，防范由于网络或私服问题导致的拉取失败：
   `docker save $(docker images -q) | gzip | aws s3 cp - s3://<prefix>/docker_images.tar.gz`

---

### [Component: site_migration (Load)]
恢复阶段也是全量流式下载，边下边解。

* **[MODIFY]** `playbooks/roles/site_migration/tasks/load.yml`
1. **pg_dump 恢复**: 
   `aws s3 cp s3://<prefix>/db/<name>.sql.gz - | gunzip -c | psql ...`
2. **Gitea / QMD / Openclaw 恢复**: 
   `aws s3 sync s3://<prefix>/xxx /local/path/`
3. **Caddy 配置**: 
   使用 Ansible template 渲染覆盖（使用刚备份的数据作为防抖备选）。
4. **容器镜像导入**: 
   `aws s3 cp s3://<prefix>/docker_images.tar.gz - | gunzip -c | docker load`

---

## 方案优势
1. **彻底解决磁盘打爆问题**：导出和拉取全部变成流式（Stream）操作，源机和目标机的本地磁盘压力骤降为 0（除了 aws s3 sync 的元数据）。
2. **极速传输**：AWS S3 底层骨干网通常比普通跨海 VPS 直连的带宽更稳定。
3. **天然灾备机制**：即使迁移中途终端，数据在 S3 上是一份完美的异地全量备份。

## 验证计划 (Verification Plan)
1. 在 Ansible 中设置临时的 AWS 环境变量进行执行（或使用动态 Vault 注入）。
2. 观察 S3 桶内是否生成了符合命名规范的前缀目录及文件。
3. 验证迁移环境的 Gitea 服务、数据库服务能否顺利读取数据并恢复运作。
