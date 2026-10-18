# `web_saas_cold_recovery`

Reusable cold-backup and cross-machine recovery orchestration for the selfhost
web-saas PostgreSQL service. It composes `web_saas_data_backup` and
`web_saas_data_restore_verify`; it does not duplicate database dump logic.

Supported modes:

- `backup`: create a new encrypted database checkpoint on the source host and
  write `SHA256SUMS`/`receipt.json`, then fetch only the encrypted archive plus
  a nonsecret JSON receipt to the controller.
- `stage`: copy the encrypted archive from the controller to a separate host's
  `/data` volume and verify its checksum without decrypting it.
- `restore_verify`: restore into a run-owned temporary database and compare
  schema, migration state, row fingerprints, and approved counts. PostgreSQL
  system identifiers are intentionally not compared across machines.
- `restore_primary`: always fails closed. Primary replacement is an operator
  procedure requiring an independent restore, approval, and a separate
  recovery runbook; this role must never overwrite a live database.

The passphrase is runtime-only (`web_saas_cold_recovery_passphrase`) and must
come from OIDC/Vault or an equivalent protected execution environment. It must
never be committed, printed, or placed in the receipt. The archive and receipt
are created with `0600` permissions; their parent directories are `0700`.

Use numeric, unique `web_saas_cold_recovery_run_id` values. A cross-host run is
complete only after both `backup` and `stage`/`restore_verify` evidence pass.
Set `web_saas_cold_recovery_service_variant` to the target profile such as
`v2`, `v3`, `vN`, `prod-v2`, or another approved name; the receipt binds this
parameter so a checkpoint cannot silently cross profiles. The role does not
assume a fixed `v2`/`v3`/`vN` value.
The role does not create cloud resources, alter DNS, write CMDB, or perform a
production deployment.
