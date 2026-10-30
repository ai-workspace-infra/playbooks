# Vault KV v2 path migration

This repository contains a generic, fail-closed copier for explicitly reviewed KV v2 field mappings. Keep environment-specific Vault paths and field inventories in an access-controlled operational plan; do not commit secret-store paths to this public repository.

## Preview

```sh
python3 scripts/vault_kv_path_migration.py --plan /secure/path/migration-plan.json
```

The preview lists paths, field names, review state, and retirement gates only. It does not contact Vault. The plan schema is JSON with `schema_version: 1`, a `migrations` array (`source`, `destination`, `fields`, `status`, optional note), and optional `retirement_candidates`. A logical path has the form `mount/path` and omits the KV v2 `/data/` API segment.

## Apply reviewed copies

Only set an entry's status to `approved` after the data owner confirms the exact source and destination Vaults, field contract, consumer, rotation decision, and whether a historical value is safe to reuse. Provide narrowly scoped, short-lived source read and destination write credentials through the environment:

```sh
export VAULT_SOURCE_ADDR="https://source-vault.example"
export VAULT_SOURCE_TOKEN="<short-lived source token>"
export VAULT_DESTINATION_ADDR="https://destination-vault.example"
export VAULT_DESTINATION_TOKEN="<short-lived destination token>"
python3 scripts/vault_kv_path_migration.py \
  --plan /secure/path/migration-plan.json \
  --apply \
  --confirm-copy "COPY REVIEWED VAULT FIELDS"
unset VAULT_SOURCE_TOKEN VAULT_DESTINATION_TOKEN
```

The implementation keeps values in memory and never prints them. Existing destination records are merged only when selected field values do not conflict; conflicting values fail without writing. KV v2 check-and-set prevents overwriting a concurrent update. Matching fields are idempotent no-ops. It never deletes or soft-deletes source records. If a Vault request times out after the server accepted a write, inspect destination metadata/field names and rerun; do not assume failure or success from the client timeout alone.

`VAULT_ADDR`/`VAULT_TOKEN` may be used when source and destination share one endpoint and identity. Optional namespace variables are `VAULT_SOURCE_NAMESPACE`, `VAULT_DESTINATION_NAMESPACE`, or shared `VAULT_NAMESPACE`.

## Retirement

The plan can mark a path or individual field as a retirement candidate. This is status documentation only and never causes deletion. Before a separate removal action, require an empty consumer inventory (including recovery readers), accepted destination runtime and data migration, explicit closure of the rollback window, resolved retention obligations, and an approved backup/archive where required. Revoke old consumer read access and observe before scheduling a separate exact-path/field deletion review.

Do not copy database DSNs that may still point to a source environment, shared service tokens, bootstrap credentials, Vault root/unseal material, or a read-only/auditor credential into a writer/admin role without a new explicit review.
