# Accounts API migration execution owner

Use `selfhost-core-data-migration.yml` or include this role from a reviewed
caller. The required `selfhost_core_data_migration_request` is the JSON input
document accepted by `scripts/control_plane/migration.py`; deliver it through
stdin, not a credential file. `policy` is GitOps accounts-migration v1 with
reciprocal HTTPS origins, dedicated auth references, scopes and UTC expiry.
`client` is a prebuilt Accounts migratectl binary; `client_sha256` verifies its
bytes before invocation. No on-target build or legacy generation refresh owner.

`selfhost_core_data_migration_action` defaults to `preview`; `status` and `apply`
are explicit. Pass original `plan_id`/`snapshot_id` to resume preview. Apply
requires `plan_id`, `plan_sha256`, `backup_receipt`, its reviewed
`backup_receipt_sha256`, `owner_proof`, `owner_proof_sha256` and `owner_sha`.
These are fetched nonsecret outputs of `selfhost_core_data_backup`, authenticated
by the caller's owner/run evidence. An optional local encrypted `backup_archive`
is rehashed if supplied; plaintext database data is never copied to the controller.
See the recovery evidence schema in the contract doc.
The owner calls `migratectl transfer-api --status` and binds the current plan to
both receipts and the actual encrypted archive before it can invoke `--apply`.
Changed target identity/schema/baseline, stale evidence, changed archives,
missing restore verification or conflicts fail closed. These files are trusted
owner-issued operational evidence, not arbitrary operator claims; the caller
must authenticate their producer/run and reviewed receipt checksum.

Credentials use only `--source-token-env`/`--target-token-env` or
`--source-auth-kv`/`--target-auth-kv`; the Accounts client resolves them in memory.
No tokens or DSNs are accepted in the request. Unsupported APIs fail with no
fallback. This role does not start listeners or initialize the independent
`accounts_migration_api` schema. That is an explicitly authorized upstream
operation; source API execution must keep the source DB read-only.

Outputs: private `selfhost_core_data_migration_evidence`, with plan/receipt and
`business_acceptance: false`, `cutover: false`. API transactions, domain scope,
collision rules and idempotency belong to Accounts. Source service changes,
target restore, DNS/writer handoff and rollback are separate authorized actions.
After an ambiguous apply, query `status` using the same plan and subject; do not
blindly replay preview or restore over a serving target.
