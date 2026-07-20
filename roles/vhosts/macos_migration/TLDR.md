# macos_migration — Obsidian Vault ↔ Google Drive

同步本机系统“文稿”目录中的 Obsidian Vault：

```text
~/Documents/Obsidian Vault  <->  gdrive:Obsidian Vault
```

## 首次初始化

首次运行以**本地 Vault 为准**初始化 Google Drive；请先确认云端目标目录可以被本机内容覆盖。

```bash
cd /Users/shenlan/workspaces/ai-workspace-infra/playbooks
ansible-playbook setup-macos-migration-google-drive-sync.yml
```

前提：本机已安装 rclone，且已通过 `rclone config` 配置 `gdrive:` remote。

## 日常同步

首次成功后，角色会安装登录用户的 LaunchAgent，每 15 分钟运行一次
`rclone bisync`。本地和 Google Drive 两端的新增、修改、删除都会同步；
同名冲突选择修改时间较新的版本。

立即手动同步：

```bash
~/.local/state/xworkspace/macos-migration/google-drive-sync/sync-obsidian-vault.sh
```

直接执行脚本时，rclone 会在终端显示实时传输速度、已传输数据与文件数。
通过 Ansible 执行时，任务改为异步运行，并每 5 秒打印一条来自
`rclone.log` 的最新统计状态。

## 同步方向

`macos_migration_google_drive_sync_mode` 支持三种模式：

- `push`：本地 Vault → Google Drive，不删除云端文件。
- `pull`：Google Drive → 本地 Vault，不删除本地文件。
- `bidirectional`（默认）：双向同步，删除也会同步。

### push：本地 Vault → Google Drive

使用 `rclone copy`，适合将本机 Vault 安全上传到云端已有目录：

```bash
ansible-playbook setup-macos-migration-google-drive-sync.yml \
  -e 'macos_migration_google_drive_destination=gdrive:ObsidianVault-Backup' \
  -e macos_migration_google_drive_sync_mode=push
```

### pull：Google Drive → 本地 Vault

使用 `rclone copy`，仅补充或更新本机文件，不删除本地已有文件：

```bash
ansible-playbook setup-macos-migration-google-drive-sync.yml \
  -e 'macos_migration_google_drive_destination=gdrive:ObsidianVault-Backup' \
  -e macos_migration_google_drive_sync_mode=pull
```

### bidirectional：本地 Vault ↔ Google Drive

首次成功运行以本地 Vault 初始化云端；之后两端的新建、修改、删除都会
相互同步，同名冲突采用修改时间较新的版本：

```bash
ansible-playbook setup-macos-migration-google-drive-sync.yml \
  -e 'macos_migration_google_drive_destination=gdrive:ObsidianVault-Backup' \
  -e macos_migration_google_drive_sync_mode=bidirectional
```

## 验证

```bash
rclone lsf "gdrive:Obsidian Vault" | head
tail -n 50 ~/.local/state/xworkspace/macos-migration/google-drive-sync/rclone.log
```

## 常用覆盖项

同步到已有云端目录：

```bash
ansible-playbook setup-macos-migration-google-drive-sync.yml \
  -e 'macos_migration_google_drive_destination=gdrive:ObsidianVault-Backup'
```

只做一次同步，不安装定时任务：

```bash
ansible-playbook setup-macos-migration-google-drive-sync.yml \
  -e macos_migration_google_drive_schedule_enabled=false
```

## 注意

- `.obsidian/workspace*.json`、`.obsidian/cache/**` 与 `.DS_Store` 不同步，避免不同设备的界面状态互相覆盖。
- 双向同步会传播删除操作；不要把它当作历史备份。重要笔记请保留独立备份或版本控制。
