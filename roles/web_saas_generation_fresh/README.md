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

Verification: focused owner tests, syntax checks, real PostgreSQL fixtures and
per-case receipts. BLOCKED/FAIL/NOT_RUN are not acceptance success even when the
Ansible recap itself succeeds.
