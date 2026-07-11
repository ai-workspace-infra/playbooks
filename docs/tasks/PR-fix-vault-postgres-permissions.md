# PR: fix/vault-postgres-permissions

## Summary
Change ownership of Vault storage tables to the `vault_storage` user.

## Context
When deploying Vault in standalone mode with PostgreSQL as the storage backend, the schema creation task `Ensure Vault PostgreSQL storage schema exists` runs as the `postgres` superuser. As a result, the created tables (`vault_kv_store` and `vault_ha_locks`) and index (`parent_path_idx`) are owned by `postgres`.

When the Vault service starts, it connects using the `vault_storage` role and attempts a migration check. This triggers a permission error:
```
Jul 11 19:15:13 xworkmate-bridge-debian-13.svc.plus vault[60043]: 2026-07-11T19:15:13.399+0800 [WARN]  storage migration check error: error="ERROR: permission denied for table vault_kv_store (SQLSTATE 42501)"
```
This causes Vault startup to fail and the API port 8200 to remain closed (Connection refused).

The fix is to explicitly transfer ownership of the created tables and indexes to `vault_storage` immediately after their creation.

## Changes
| File | Change |
|------|--------|
| `roles/vhosts/vault/tasks/main.yml` | Run `ALTER TABLE ... OWNER TO vault_storage` for `vault_kv_store` and `vault_ha_locks`, and `ALTER INDEX ... OWNER TO vault_storage` for `parent_path_idx`. |

## Related
- Console repo PR: #28
- Playbooks PR: #116
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- CI/CD workflow `deploy-ai-workspace-iac.yaml` should deploy successfully.
- Both Debian 13 and Ubuntu 26 hosts should pass the Vault readiness checks.
