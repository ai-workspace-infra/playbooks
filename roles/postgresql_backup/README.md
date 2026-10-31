# postgresql_backup

Owner: Playbooks. Caller: explicit Ansible playbook; Toolkit owns selection/authorization only.

Imported from Platform-Orchestrator `scripts/backup` as the owner-first migration. The existing Toolkit copy is frozen until this owner PR is merged and the caller route is verified. No production execution is part of this PR.

Set `postgresql_backup_enabled: true`, `postgresql_backup_action`, `postgresql_backup_environment` (runtime Vault/backup environment variables), and `postgresql_backup_arguments` (a list) explicitly. Actions: backup. No operation runs by default or in check mode. Restore additionally requires `postgresql_backup_restore_approved: true` following target-specific authorization.

Use an authorized management/backup identity; auditor is not permitted to export raw secrets or restore. Run on the explicit backup execution host; `keychain` requires a macOS host with an unlocked authorized Keychain. Commands never install dependencies. Execution logs containing credentials are suppressed. Credential values must not be committed.

Vault `export` accepts one output JSON path; `restore` accepts one encrypted archive path. KV backup/restore archives and full KV/policy/role exports are different formats and must not be interchanged. Full export does not contain Raft snapshots or unseal material. PostgreSQL backup uses the existing Docker database layout; K3s support is not implied.

This imports legacy behavior for review, not recovery acceptance. A non-production restore drill remains required before retiring the old route. The existing `selfhost_postgresql_daily_dump` role remains the independent scheduled on-host dump implementation.
