# PR: fix/pg-admin-password-fallback

## Summary
Add fallback to `postgresql_admin_password` Ansible variable and shadow password file in `x_memory_hub` and `litellm` defaults.

## Context
When executing database role creation/provisioning tasks in `x_memory_hub` and `litellm` roles, the tasks execute `psql` as the admin user (`postgres`) and authenticate using the password defined in `x_memory_hub_pg_root_password` or `litellm_database_admin_password`.

These variables default to looking up the shell environment variables `POSTGRESQL_ADMIN_PASSWORD` or `POSTGRES_PASSWORD`. However, in automated GHA deployments or bootstrapping via `setup-ai-workspace-all-in-one.sh`, the password is passed to Ansible as an Ansible extra-variable (`-e postgresql_admin_password=...`), not as shell environment variables.

Consequently, `x_memory_hub_pg_root_password` evaluates to empty, leading to connection failures on role creation:
```
psql: error: connection to server at "127.0.0.1", port 5432 failed: fe_sendauth: no password supplied
```

The fix adds a robust fallback mechanism to both `defaults/main.yml` files:
1. First, check the Ansible variable `postgresql_admin_password` (which captures the CLI `-e` input).
2. Fall back to looking up env variables `POSTGRESQL_ADMIN_PASSWORD` or `POSTGRES_PASSWORD`.
3. Fall back to reading the persistent local shadow password files (`/root/.ai_workspace_postgres_password` on Linux or `~/.ai_workspace_postgres_password` on macOS).
4. Finally, default to `'postgres'` or `''` as a safety baseline.

## Changes
| File | Change |
|------|--------|
| `roles/vhosts/x_memory_hub/defaults/main.yml` | Update `x_memory_hub_pg_root_password` fallback chain. |
| `roles/vhosts/litellm/defaults/main.yml` | Update `litellm_database_admin_password` fallback chain. |

## Related
- GHA Run: 29150998510
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- CI/CD workflow `deploy-ai-workspace-iac.yaml` should deploy successfully.
- Database roles for `x_memory_hub` and `litellm` should be successfully provisioned.
