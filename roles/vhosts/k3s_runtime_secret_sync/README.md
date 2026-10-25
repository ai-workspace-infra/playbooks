# K3s runtime secret field reconciliation

Runs locally using an explicit HTTPS Vault address and runtime token. Supply
`k3s_runtime_secret_sync_apply=true`, `k3s_runtime_secret_sync_vault_addr`,
`k3s_runtime_secret_sync_token`, and a bounded `k3s_runtime_secret_sync_items`
list of `{source, target, fields}`. Paths are relative to the KV mount, without
`kv/` or `data/`. Each source must contain every selected field.

Read and write identities must be scoped to the declared paths. No credentials
belong in inventory, committed variables or command arguments. Use an approved
runtime lookup for the token. The role uses KV v2 compare-and-set, preserves
unrelated target fields, and writes only changed values. ESO refresh and rolling
service restart are separate deployment steps. It performs no DNS changes.

Before apply retain the target's KV version. Rollback requires reviewing the
previous version and current consumers; do not blindly restore an entire record
if other fields have changed. The role never deletes KV versions.

Run `ansible-playbook reconcile_k3s_runtime_secrets.yml --syntax-check` before
applying with the explicit runtime variables. Missing source fields and concurrent
writes fail rather than silently replacing credentials.
