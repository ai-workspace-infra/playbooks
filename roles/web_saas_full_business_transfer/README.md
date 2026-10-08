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
`source` (PROD Supabase project/endpoint, required TLS, optional pre-approved
connection identity SHA-256, and one-way prod-supabase-to-prod-selfhost direction).
`ready=false` is accepted when the caller resolves the authorized Serverless DSN
from Vault; the actual connection identity is bound in the sanitized receipt.

Runtime secrets arrive only from the caller's OIDC→Vault outputs. The source DSN
may use `postgres` or `readonly_release` for the configured project; the owner
forces connection and transaction read-only mode. Session-pooler port 5432 and
TLS are required; transaction pooling, weaker TLS or other projects fail.

Actions take explicit `preview`, `copy`, `compare` or `core_users`. All require the
caller's production data-gate assertion, with the configured release-tag review
policy enforced by Toolkit. Preview reads catalogs/user keys. Full-business copy
requires all 53 target tables empty; compare proves the full-business scope.

`core_users` permits existing target users. The pinned Accounts tool matches by
normalized email, preserves target user UUIDs and updates email/password hash/
PROD Proxy UUID. Missing users are inserted, target-only emails are refused, and
actual source/target count and three digests must match. No dynamic business
rows are copied or compared in this mode. Schema version and target identity
checks remain mandatory. Repeating core synchronization is supported.
The role runs a reviewed prebuilt binary; it never builds an image on the target.

The sanitized receipt is `prod-full-business-receipt.json` in RUNNER_TEMP. It
contains source connection/snapshot/catalog hashes, exact image/schema identity,
UTC snapshot times, and a `core_users` proof covering the latest email-keyed
user count plus email, password-hash and authoritative Proxy UUID digests for
both source and target. No user rows or password material are emitted. Dynamic
business-table proofs remain available for the data-owner operation, but the
Cloudflare cutover contract consumes the core-user proof only. Arbitrary extra
stdout fields are removed. Preview cannot impersonate copy/compare. Every receipt keeps
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
  `web-saas-caddy` is excluded from the writer guard and active-writer diagnostic;
  it remains running to serve HTTPS throughout migration and comparison.

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
accepted original source/rollback point. After full-business copy, successful replay is refused;
use approved compare/recovery evidence rather than resetting copied rows. The
core-user reconciliation mode supports populated targets and repeated synchronization. Final
source writer freeze/catch-up, <=10-minute full equality, single-writer proof,
Accounts+Billing coordinated Edge/CNAME cutover and production acceptance remain
separate stages. Existing UAT dataset reconciliation and full-upgrade/rollback
qualification also remain separate; a new empty baseline is not that acceptance.

## Narrow acceptance modes

`core_users_compare` invokes only `compare-core-users` and performs a read-only
`SELECT 1` on the target. It can run while services remain active, never stops
Caddy, and publishes only count/email/password-hash/Proxy-UUID equality. It does
not claim 53-table equality or require a prior copy receipt. Core connection
metadata omitted by the CLI is reconstructed from the exact validated runtime
DSN passed to that command; a supplied differing identity remains refused.

`.github/actions/prod-availability` separately observes a fresh successful
Doco-CD poll with no new polling/deployment error, idle reconciliation, target
container health, certificate-verified HTTPS on the host and directly from the
runner to the CMDB IP, Accounts/Billing health endpoints, and read-only `SELECT 1`.
It never starts/stops services or changes DNS. Missing evidence fails the check.
Manual business testing and DNS/cutover authorization remain independent.
