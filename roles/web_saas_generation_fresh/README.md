# Fresh generation rehearsal

Target-only manual owner for native initialization, reviewed `migratectl` data
alignment, encrypted populated backup and independent restore. Never resets an
existing database. Source access is to a retained read-only snapshot on the target;
it does not freeze or modify the serving generation.

Inputs: one verified target inventory; `generation_fresh_manifest_file` from a
fixed GitOps checkout; `generation_fresh_billing_sql`; `generation_fresh_spec`
with adjacent source/target generations, unique checkpoint directory, exact tool
source/binary hashes, retained binary path, schema version and artifact hashes,
isolated source database, restore receipt and archive digest, plus action.
Backup credentials are read from Vault at execution only. No inventory or secret
values belong in the repository.

Entrypoints: `web-saas-generation-fresh.yml` with actions `initialize`,
`backup-restore`, `preview`, `align`, `verify`, `post-sync-restore` in that order.
`web-saas-generation-application.yml` prepares read-only candidate applications;
`select-doco-cd-release.yml` selects the fixed declaration. Follow with
`web-saas-generation-regression.yml` using explicit case IDs and candidate SHA.
Each invocation uses `ansible-playbook -i <verified-inventory> <entrypoint>
-e @<private-vars>`; no defaults select a live host.

Existing checkpoints are revalidated against database OID, native schema and
applicable retained restore/row/permission fingerprints. They are not blindly
treated as renewed acceptance. Target application writers must be absent during
data operations. Recreated containers receive only the retained hash-verified
tool. Comparison explicitly disables preview mode to actually compare rows.

When GitOps explicitly sets `target.candidate_auth_sessions: true`, Accounts uses
an independent `generation_auth` identity with session-table INSERT/UPDATE/DELETE
only. Effective write grants on every other public table must be absent. Billing
keeps the read-only `generation_app` identity. This supports candidate login;
it does not transfer production business write ownership. New target-local login
sessions are operational facts and must not be removed to force full snapshot
equality. Record any resulting comparison difference explicitly.

Keep all databases, backups and failure receipts. Recovery is stop-and-inspect,
not automatic database overwrite or downgrade. Public traffic and writer handoff
are outside these entrypoints and require a separate approved cutover plan.

## Explicit business runtime preparation

The application entrypoint defaults to `generation_fresh_spec.runtime_mode:
candidate`. After an authorized writer handoff, use `primary` with
`generation_fresh_primary_confirm: true`, an adjacent `source_generation`, and
`primary_backup_manifest` pointing to the populated target's verified online
backup manifest. The inventory must contain both verified hosts. The task reads
source state only: both reconcilers and Accounts/Billing must already be stopped,
and source application database sessions must be drained. It does not pause the
serving generation or select DNS automatically.

Primary preparation grants `generation_app` SELECT/INSERT/UPDATE/DELETE on
existing public business tables and USAGE/SELECT on their owned sequences.
Schema migration and migration-tool tables remain outside DML scope. No schema
CREATE, TRUNCATE, ownership, role membership, administrative privilege or new RLS
bypass is granted. Existing unexpected administrative grants stop the transaction.
`readonly_release` is exclusively a backup/audit/source-reader identity; this
task never changes it. Migration writes use the separately reviewed executor.

Both application env files select `generation_app` in primary mode, replacing
Accounts' candidate session-only connection without granting `generation_auth`
business privileges or rotating any password. The generated runtime enables
background writers and removes the candidate ingress method restriction.
The pinned GitOps Compose declaration must also select the matching primary
profile; its explicit environment settings override env files. Primary mode
preparation alone does not deploy containers or prove SMTP/OAuth/payment readiness.

Before pausing either side, `web-saas-generation-primary-config.yml` stages the
declared primary SMTP, OAuth and payment settings plus `account-primary.yaml`.
It uses the pinned `runtime/<target>-primary.yaml` and fails on missing Vault
fields. Existing candidate containers do not mount these files. The initial
primary declaration deliberately retains the serving generation's Stripe test
mode; live payments require a separate reviewed declaration. The primary Compose
profile loads the credential env file as raw values to avoid interpolating secret
characters. No credential is printed or written to the local controller checkout.

For a disposable PostgreSQL 17 container named
`generation-permissions-fixture-<unique-run>`, run
`GENERATION_PERMISSIONS_PG_CONTAINER=<container> python -m unittest discover
-s tests/data_operations -p test_generation_application_permissions.py -v`.
Use a fresh empty fixture each invocation; the test deliberately refuses an
existing database instead of clearing it. The suite exercises business writes,
denied DDL/audit writes, transaction rollback and idempotent grants.

Re-running primary preparation is safe. Candidate preparation refuses an already
promoted role instead of silently restoring read-only mode. Rollback requires
stopping the new writer and reconciling new facts before reopening an old writer;
this task does not downgrade or overwrite databases automatically.

Verification: focused owner tests, syntax checks, real PostgreSQL fixtures and
per-case receipts. BLOCKED/FAIL/NOT_RUN are not acceptance success even when the
Ansible recap itself succeeds.
