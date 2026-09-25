# Vault legacy migration

Converts the existing `vault.svc.plus` host from local PostgreSQL storage to
single-node Raft **in place**, reading PostgreSQL exactly once over
`127.0.0.1`. It never restarts, reconfigures, or writes PostgreSQL, and it
never runs `vault operator init` or touches unseal shares/root tokens — those
stay a manual operator procedure (`docs/vault/operator-runbook.md`).

`vault_legacy_migration_action` selects one of three destructive operations,
each gated by an exact `vault_legacy_migration_confirm` phrase:

| Action | Confirm phrase | Does |
| --- | --- | --- |
| `convert` | `CONVERT-VAULT-TO-RAFT` | backup, stop, `vault operator migrate` (via the existing `vhosts/vault/files/vault_operator_data.sh migrate-offline`), install the port guard |
| `rollback` | `ROLLBACK-VAULT-TO-POSTGRESQL` | restore the PostgreSQL-backed configuration from the backup `convert` wrote |
| `retire` | `REMOVE-LEGACY-VAULT-PEER` | stop and disable Vault after its Raft peer has been removed |

`convert` is idempotent: it inspects the live `vault.hcl` and is a no-op if
the host is already Raft-backed (which it is after a successful conversion
and the subsequent `deploy_vault_single_raft.yml` run writes the Raft
config). It leaves Vault **stopped**; `deploy_vault_legacy_migration.yml`
imports `deploy_vault_single_raft.yml` afterward to write the Raft
configuration and start the service — operators still perform the manual
unseal.

Requires `vault_legacy_migration_node_id` (defaults to `inventory_hostname`)
and a private `vault_legacy_migration_overlay_address` (defaults to the
`vault_shared_private_ip` host var that the provider-neutral node contract
already sets for the migration source). Raft must never bind a public
address; the role asserts this.

See `vhosts/vault_port_guard` for the companion role that keeps 8200/8201 off
everything except loopback and the declared overlay interface.
