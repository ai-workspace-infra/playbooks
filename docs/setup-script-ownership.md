# Setup installer ownership migration

These host/service installers belong to Playbooks. Compatibility executors are grouped under their service role and exposed through an explicit opt-in task file. Existing normal role entrypoints are preserved.

| Previous Toolkit script | Playbooks owner role | Entry |
| --- | --- | --- |
| `setup-ai-memory-hub.sh` | `ai_memory_hub` | `tasks_from: legacy_setup` |
| `setup-xworkmate-workspace.sh` | `xworkmate_workspace` | `tasks_from: legacy_setup` |
| `setup-agent-context.sh` | `xworkmate_workspace` | `tasks_from: legacy_setup` |
| `setup-gitea.sh` | `vhosts/gitea` | `tasks_from: legacy_setup` |
| `setup-vault-zitadel.sh` | `vhosts/vault_zitadel_bootstrap` | `tasks_from: legacy_setup` |
| `setup-qmd-storage.sh` | `ai_memory_hub` | Alias of `setup-ai-memory-hub.sh`; do not keep a second implementation. |

QMD Storage and AI Memory Hub differ only in the health-loop increment. The retained version uses `ATTEMPT=$((ATTEMPT + 1))`, avoiding `set -e` exit when the previous value is zero.

Owner-first cutover: merge this owner PR, record the reviewed Playbooks SHA, switch manual/CI callers, verify their non-mutating routes, then remove Toolkit copies. This PR does not delete active callers or execute installers. `vhosts/gitea` system-package tasks and `docker/zitadel` remain their existing supported deployment modes; the imported Docker Gitea and combined Vault/Zitadel installers are explicitly labeled compatibility paths.
