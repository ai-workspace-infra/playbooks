# Web SaaS Selfhost incremental data migration

## Current status: blocked at the execution adapter

This role validates the request and then blocks before any database command
until the Selfhost execution adapter is reviewed and wired. It does not invent
an adapter from workflow inputs.
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

The official Accounts `cmd/migratectl migrate` now supports a bounded release
interface: `--expected-version`, `--target-version`, `--migration-sha256`,
`--lock-timeout`, and `--statement-timeout`. It refuses dirty state, unexpected
pending migrations, and checksum/version mismatches while holding a database
advisory lock. The existing
`scripts/data_operations/serverless/apply_accounts_incremental_schema.sh` is
still Supabase-pooler specific and is not called by this role.

Before enabling execution, the Playbooks owner must provide reviewable evidence
that the Selfhost adapter invokes that bounded interface inside the exact
`canonicalAccount` target and its source/tests:

1. A documented invocation of the bounded Accounts interface that runs in the
   `canonicalAccount` target and takes its DSN only from an environment
   variable, with secret output suppressed.
2. A real backup and isolated-restore receipt carrying the environment,
   target database identity, baseline identity, expected schema version,
   checkpoint/archive digest, and successful restore result, followed by a
   sanitized migration receipt.

The `selfhost-data-lifecycle` workflow-call path should stop after preflight
and backup. It must not expose this migration phase until the execution
adapter exists. The baseline manifest validator does not
authenticate its approval metadata;
the baseline status is `manifest_validated`, not a claim that database rows
were frozen. Until the execution adapter exists with source-level tests, this
role must remain blocked. The current
`web_saas_release_upgrade` backup receipt does not use the paired agentA
backup/restore evidence contract and is not accepted here.
