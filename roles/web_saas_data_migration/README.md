# Web SaaS Selfhost incremental data migration

## Current status: bounded migration component, not release acceptance

This role validates the request and paired encrypted isolated-restore evidence,
then invokes the reviewed Accounts bounded migrator twice inside the exact
environment-bound Accounts container (`canonicalAccount` for UAT;
`web-saas-accounts` for a versioned PROD generation). It emits only a sanitized component receipt;
release acceptance remains blocked until independent data, login, subscription,
and application compatibility probes pass.
The migration is in-place on the same UAT Selfhost target database; source
provenance is carried only by the separate baseline manifest. It does not
receive a DSN, invoke Vault, initialize or reset a schema, or clear a dirty
migration marker. A validated
request requires a validated baseline manifest, the exact
environment-bound container, expected
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

The official Accounts `cmd/migratectl migrate` supports a bounded release
interface: `--expected-version`, `--target-version`, `--migration-sha256`,
`--lock-timeout`, and `--statement-timeout`. It refuses dirty state, unexpected
pending migrations, and checksum/version mismatches while holding a database
advisory lock. The existing
`scripts/data_operations/serverless/apply_accounts_incremental_schema.sh` is
still Supabase-pooler specific and is not called by this role.

The `selfhost-data-lifecycle` workflow-call path builds the immutable Accounts
source at `accounts_source_revision`, stages only the binary and migration
files, and invokes `migratectl` with `--dsn-env DATABASE_URL`; command output is
suppressed. The role requires a real backup and isolated-restore receipt before
execution and binds its sanitized component receipt to the same candidate,
database, baseline, checkpoint, source revision, version and checksum. The
baseline manifest validator does not
authenticate its approval metadata;
the baseline status is `manifest_validated`, not a claim that database rows
were frozen. This component receipt is not full release acceptance. The current
`web_saas_release_upgrade` backup receipt does not use the paired
backup/restore evidence contract and is not accepted here.

For versioned production generations, the request must bind the exact
`web-saas-prod-vN` host and the run ID shared by the encrypted backup and
isolated restore. Stable `prod` is intentionally rejected by this migration
role. The separate `web-saas-generation-data-operations.yml` entry point
requires that exact inventory host and performs a target-host encrypted backup
plus isolated restore before an optional single migration. It never imports
data from another generation, stops or restarts services, changes GitOps, or
changes public DNS. Application-release and business-acceptance gates remain
separate.
