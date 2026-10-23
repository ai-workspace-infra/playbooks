# Fresh generation rehearsal

Target-only manual owner for native initialization, reviewed `migratectl` data
alignment, encrypted populated backup and independent restore. Never resets an
existing database. Source access is to a retained read-only snapshot on the target;
it does not freeze or modify the serving generation. The V4 business writer
continues through stages 0-7. A separate manual cutover at stage 8 owns any
source-writer fence and one-writer handoff. The `--writers-paused` comparison
gate applies to candidate/target writers, not the live source writer.

Inputs: one verified target inventory; `generation_fresh_manifest_file` from a
fixed GitOps checkout; `generation_fresh_billing_sql`; `generation_fresh_spec`
with adjacent source/target generations, unique checkpoint directory, exact tool
source/binary hashes, retained binary path, schema version and artifact hashes,
isolated source database, restore receipt and archive digest, plus action.
Backup credentials are read from Vault at execution only. No inventory or secret
values belong in the repository.

Entrypoints: `web-saas-generation-fresh.yml` with actions `initialize`,
`backup-restore`, `preview`, `align`, `verify`, `post-sync-restore` in that order.
`web-saas-generation-application.yml` prepares isolated candidate applications;
`select-doco-cd-release.yml` selects the fixed declaration. Follow with
`web-saas-generation-regression.yml` using explicit case IDs and candidate SHA.
Each invocation uses `ansible-playbook -i <verified-inventory> <entrypoint>
-e @<private-vars>`; no defaults select a live host.

Existing checkpoints are revalidated against database OID, native schema and
applicable retained restore/row/permission fingerprints. They are not blindly
treated as renewed acceptance. Target application writers must be absent during
data operations. Recreated containers receive only the retained hash-verified
tool. Comparison explicitly disables preview mode to actually compare rows.

Accounts and Billing always reuse `web_saas_business_database` and the canonical
`account_user` identity declared by GitOps. Its password is read from the existing
Vault owner at execution, never derived from recovery entropy. No session-only
or alternate business identity is created. Candidate isolation is enforced by
ingress, disabled background writers and external integration isolation, not by
substituting a broken business principal. New target-local login sessions are
operational facts and must not be removed to force snapshot equality.

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

The shared database owner grants `account_user` business DML and sequence access
without schema CREATE, TRUNCATE, ownership, role membership or administration.
Native backend RLS bypass is explicitly selected by the GitOps contract; the
restorer never changes table owners to bypass policies. Unexpected grants stop
the transaction. Schema migration and migration-tool tables remain excluded.
`readonly_release` is exclusively a backup/audit/source-reader identity; this
task never changes it. Migration writes use the separately reviewed executor.

Both applications select the same raw `business-database.env`. Preparation does
not rotate a password or enable background writers. Primary configuration
removes the candidate ingress restriction only after the existing source fence.
The pinned GitOps Compose declaration must also select the matching primary
profile; its explicit environment settings override env files. Primary mode
preparation alone does not deploy containers or prove SMTP/OAuth/payment readiness.

Before pausing either side, `web-saas-generation-primary-config.yml` stages the
declared primary SMTP, OAuth and payment settings plus `account-primary.yaml`.
It uses the pinned `runtime/<target>-primary.yaml` and fails on missing Vault
fields. Existing candidate containers do not mount these files. The initial
primary declaration deliberately retains the serving generation's Stripe test
mode; live payments require a separate reviewed declaration. The primary Compose
profile uses ordinary env files supported by the pinned CD parser. Secret values
are quoted/escaped by the template, with literal dollar signs protected from
interpolation. No credential is printed or written to the controller checkout.

For a disposable PostgreSQL 17 container named
`generation-permissions-fixture-<unique-run>`, run
`GENERATION_PERMISSIONS_PG_CONTAINER=<container> python -m unittest discover
-s tests/data_operations -p test_generation_application_permissions.py -v`.
Use a fresh empty fixture each invocation; the test deliberately refuses an
existing database instead of clearing it. The suite exercises business writes,
denied DDL/audit writes, transaction rollback and idempotent grants.

Re-running preparation is safe. Candidate preparation does not silently revoke
business permissions or reset credentials. Rollback requires
stopping the new writer and reconciling new facts before reopening an old writer;
this task does not downgrade or overwrite databases automatically.

Verification: focused owner tests, syntax checks, real PostgreSQL fixtures and
per-case receipts. BLOCKED/FAIL/NOT_RUN are not acceptance success even when the
Ansible recap itself succeeds.

## Explicit Sandbox payment binding on an existing deployment

`web-saas-generation-stripe-config.yml` repairs an already authorized generation
whose active Compose profile reads empty payment keys from the recovery env file.
Provide `generation_stripe_target`, `generation_stripe_generation`,
`generation_stripe_gitops_checkout` and its exact `generation_stripe_gitops_sha`
through protected operational inputs. It requires the reviewed primary runtime
declaration and Sandbox credential fields, validates the actual hostname,
immutable running image and Compose revision, then changes only the two payment
keys in the existing root-only env file. Credentials remain in task memory and
the protected host file; live Stripe keys are rejected.

The owner validates the same existing Compose layers and recreates only Accounts
when its env or actual process credentials differ. A retry also repairs a prior
interruption between file update and process reconciliation. Matching reruns do
not recreate Accounts. This action does not select the full primary profile,
enable background writers, change SMTP/OAuth/database credentials or create a
checkout, charge or refund. Verify the actual process, readonly price mappings
and authenticated application preview separately after binding.
