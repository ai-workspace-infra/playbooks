# PROD full-business transfer owner

Owner: Playbooks host/database role. Caller: Toolkit `selfhost-orchestrator.yml`
with a reviewed immutable Playbooks ref. IaC supplies original accepted CMDB and
same-run private access; Toolkit verifies independent production review and real
resource/standby/native-init/Billing-parent artifacts before retrieving secrets.
This role neither creates cloud resources nor changes DNS/authoritative inventory.

## Inputs and output

The fixed non-secret spec has `initialization` (Accounts native52 manifest),
`transfer` (full-SHA/digest prebuilt Accounts tool, unchanged native SQL hash,
Billing SQL hash, clean 2026100701, sorted53 tables, batch1000, cutoverfalse), and
`source` (ready=true, readonly_release, TLSrequired, exact approved connection
identity SHA-256 and one-way prod-supabase-to-prod-selfhost direction).
A not-yet-approved source stays ready=false; do not derive/accept an arbitrary
live connection fingerprint to fill a missing reviewed source identity.

Runtime secrets arrive only from the caller's OIDC→Vault outputs: dedicated source
readonly DSN, target root password and registry credentials. Source connection is
bound to the reviewed Supabase session-pooler login/project/host/database and TLS;
transaction-pooler port, admin role, plaintext TLS fallback or other project fail.
Actual SQL role/privilege/RLS/full scope is independently checked by migratectl.

Actions take explicit `preview`, `copy` or `compare`. All require the caller's
independent production data-gate assertion. Preview inspects catalogs/user keys;
it neither writes nor streams large business tables. Copy requires all53 target
business tables empty; populated data is never reset, truncated, deleted or
upserted. Compare permits populated target and must prove every53 table's row
count/full-field digest, source identity and email/PROD Proxy/user population.
The role runs a reviewed prebuilt binary; it never builds an image on the target.

The sanitized receipt is `prod-full-business-receipt.json` in RUNNER_TEMP. It
contains source connection/snapshot/catalog hashes, exact image/schema identity,
UTC snapshot times, all53 row/digest proofs and mode flags. Arbitrary extra stdout
fields are removed. Preview cannot impersonate copy/compare. Every receipt keeps
`source_writers_paused=false`, `final_catchup_complete=false` and
`database_cutover_approved=false`: baseline/point-in-time equality cannot authorize
a primary switch while source applications/background writers remain active.

## Host and credential boundaries

- Original CMDB/checksum, same-run key, private target endpoint and fixed GitOps
  source are consumed through the shared native access guard, never fabricated.
- Only canonical PROD host/account database with independent /data disk, actual
  PostgreSQL17 container and stopped Accounts/Billing/reconcilers can execute.
- Host helper performs a digest-pinned pull and compares its compiled native
  manifest before source access. Source/target connections travel via SSH-pipelined
  module stdin and the container's stdin. They do not appear in command arguments,
  host env files, Docker create environment configuration or artifacts.
- Registry login config is private0700 in verified /dev/shm tmpfs and removed even
  on failure. The owned execution container is forcibly removed after client
  timeout/failure as well as success; secrets never persist in Docker config.
- The role does not start applications, stop a writer silently, change restart
  policy, apply schema/history/seeds, write the source, mutate cloud resources or
  move DNS. Apps/reconcilers must already be stopped by their owning stage.

## Qualification and rollout

Credential-free tests reject unready source/admin/project/TLS/port, wrong image or
scope, partial/mode-forged receipts and missing independent gate; they qualify
stdin transport, registry/container cleanup and sanitized evidence.
A dedicated disposable PG17/Docker CI fixture uses fixed Accounts source only to
build a CI qualification binary/container, then executes the exact production
stdin bootstrap/argv with synthetic loopback databases. It proves preview,
53-table baseline/compare, 1003-row batching/exact integers, and no credentials in
Docker's persistent config. It is not a deployment artifact or live acceptance.

Owner implementation → fixed Toolkit caller → non-mutating/live route verification
→ legacy deletion. There is no existing equivalent full53 logical-copy executor to
remove: old three-table import and encrypted readonly snapshot archival have
separate scopes/callers and remain in place. Do not delete a called legacy path.
Before real copy retain release-specific target-empty/schema/disk proof and the
accepted original source/rollback point. After copy, successful replay is refused;
use approved compare/recovery evidence rather than resetting copied rows. Final
source writer freeze/catch-up, <=10-minute full equality, single-writer proof,
Accounts+Billing coordinated Edge/CNAME cutover and production acceptance remain
separate stages. Existing UAT dataset reconciliation and full-upgrade/rollback
qualification also remain separate; a new empty baseline is not that acceptance.
