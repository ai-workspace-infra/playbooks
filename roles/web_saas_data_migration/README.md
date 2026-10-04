# Web SaaS Selfhost incremental data migration

## Current status: blocked

This role validates the request and then blocks before any database command.
The migration is in-place on the same UAT Selfhost target database; source
provenance is carried only by the separate baseline manifest. It does not
connect to a database, run SQL, invoke Vault, initialize or reset a schema, or
claim a migration passed. It never clears a dirty migration marker. A validated
request requires a validated baseline manifest, the exact
`canonicalAccount` container, expected
and target schema versions including an observed clean expected version,
an immutable candidate SHA-256 and Accounts source commit, explicit migration
approval bound to the target/baseline/candidate/source commit, one lowercase
SHA-256 migration checksum, an
explicit single-migration boundary, an advisory lock requirement, bounded lock
and statement timeouts. It consumes the paired backup and isolated-restore
component evidence emitted by `web_saas_data_backup` and
`web_saas_data_restore_verify`, checking exact target identity, baseline,
checkpoint/run, schema version, archive and schema checksums, database system
identity, sample counts, `data_sha256` covering full-table rows and sequence states,
and restore equality. The schema hash must use stable normalization so
PostgreSQL 17 randomized `\\restrict` lines do not produce false mismatches.
Those evidence objects are shape-checked only. The workflow-call issuer must
bind the actual producer run, source revision and receipt artifacts to trusted
workflow provenance before treating them as evidence; this role cannot
authenticate caller-supplied claims.

The current official Accounts `cmd/migratectl migrate` implementation applies
all pending migrations. It has no target-version ceiling, expected-version
precondition, migration-checksum argument, or configurable advisory-lock and
timeout contract. The existing
`scripts/data_operations/serverless/apply_accounts_incremental_schema.sh` is
explicitly UAT Supabase pooler specific; it validates a single migration
checksum and post-state but does not provide the required Selfhost execution
contract. Neither is called by this role.

Before enabling execution, the Accounts owner must provide reviewable evidence
in the form of an official Selfhost migrator interface and its source/tests:

1. A documented invocation that runs in the `canonicalAccount` container and
   takes its DSN only from an environment variable, with secret output
   suppressed.
2. An exact expected version, exact target version, and refusal to apply any
   version outside that single reviewed boundary.
3. In-tool verification that the selected migration file matches the approved
   SHA-256 digest.
4. An advisory lock held across preflight, migration, and postflight, with
   bounded lock and statement timeouts.
5. Dirty-state refusal with no force-clear/reset path, plus clean exact-version
   checks before and after execution and safe re-entry after interruption.
6. A real backup and isolated-restore receipt carrying the environment,
   target database identity, baseline identity, expected schema version,
   checkpoint/archive digest, and successful restore result.

The `selfhost-data-lifecycle` workflow-call path should stop after preflight
and backup. It must not expose this migration phase until the official
execution contract exists. The baseline manifest validator does not
authenticate its approval metadata;
the baseline status is `manifest_validated`, not a claim that database rows
were frozen. Until the official execution contract exists
with source-level tests, this role must remain blocked. The current
`web_saas_release_upgrade` backup receipt does not use the paired agentA
backup/restore evidence contract and is not accepted here.
