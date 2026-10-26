# Dev web-saas native initialization owner

Scope: new SIT/UAT namespaces only, each with its own PostgreSQL instance and
`account` database. Accounts and Billing use separate DML identities in that same
namespace database: Billing's reviewed schema references Accounts `public.users`.
This is cross-namespace data/identity isolation, not absolute table isolation
between services in one namespace. Production native-init guards remain unchanged.

## Fixed producer contract

Accounts source `8d1b09a6f2022af221b2a817d516952af41ced0e`, native SQL SHA256
`e223518d156308c1cb2ea6919cfdccf1c0cacc03b55f9b41824ef29e4f632b13`, migration
version `2026102501`, 77 business tables. The actual fixed image's
`/usr/local/bin/migratectl native-schema` must match the reviewed manifest before
`init --environment sit|uat --dsn-env NATIVE_TARGET_DSN --schema-sha256 <hash>
--writers-paused --dry-run=false` is allowed. Unknown/nonempty targets are refused;
this owner never clears a database or rewrites migration versions.

Billing source `77557d796a66dcd3373d7731125890bd91ebc5d3`, file
`sql/billing-service-schema.sql`, SHA256
`7faf46295b419102e4bbcb8cef67db14ba3beef1d46ee945fd4acfb86541c39b`.
After native init, execute that exact source SQL as DDL owner to add the real missing
`cloud_vendor_costs` table/index. Existing shared tables must match the native
manifest; do not generate substitute schemas or duplicate users into another DB.

`files/contracts.py` verifies these source bytes and supplies routing hashes and
minimal explicit DML grants. The role installs `files/seed.py` and passes a public request over stdin.
The trusted caller/operational workflow integration and remote execution remain
NOT_RUN; this PR does not add a dispatch entry or grant execution authority.
No arbitrary SQL, source URLs, environments, or table lists may come from PR input.

## Inputs and validation

`dev_web_saas_seed_request` contains only phase (`seed-admin`/`initialize`), stage,
namespace, candidate_sha, instance_id, node_uid and cluster_uid. The inventory
must be IaC CMDB, with matching `cmdb_instance_id`. The caller authenticates CMDB,
GitOps candidate and authorization before invoking this fixed owner. The owner
rechecks live cluster/node UIDs, namespace stage and one observed lane GitRepository
at that candidate (SIT commit; UAT main branch). No business credential is an input.
There is no fallback into the old production native-init owner.

The entry playbook is `dev_web_saas_seed.yml`; it selects only the caller-projected
CMDB target and repeats the instance binding. Syntax check uses
`ansible-playbook -i inventory/terraform_cmdb.py dev_web_saas_seed.yml --syntax-check -e dev_web_saas_seed_target=all` (syntax only; not an execution selector).
Unit checks: `python -m pytest tests/test_dev_web_saas_seed.py`.
Opt-in real local PG17 check: set `PG17_BIN`, `SEED_MIGRATECTL` (built from the fixed
Accounts source), `SEED_ACCOUNTS_SOURCE`, then run
`python tests/dev_web_saas_seed_pg_integration.py`. It creates a private temporary
PG cluster on a free loopback port, authenticates business roles with SCRAM, and
removes only its own test cluster. This is not K3s or cross-namespace acceptance.

## Ordered execution contract

1. Trusted Flux owner prepares namespace/RBAC/registry secret.
2. Seed owner creates the namespace-scoped PostgreSQL management Secret; random
   values exist only in memory and Kubernetes Secret. Reuse owned existing values;
   do not rotate or adopt unknown existing Secrets.
3. Flux owner submits the fixed candidate without waiting for all apps Ready.
4. Seed owner waits for exact PostgreSQL image/Ready, verifies paused writers,
   creates independent DDL owner plus non-owner DML roles, performs native init and
   fixed Billing SQL, then applies grants. No DDL/admin secret enters app Pods.
5. Runtime secrets include Accounts DATABASE_URL, DATABASE_IDENTITY_SHA256,
   ROOT_BOOTSTRAP_PASSWORD and independent per-namespace JWT/internal tokens;
   Billing DATABASE_URL uses the same account DB and its own role/password.
   The hash is Go JSON `Host/Port/Database/Role` routing identity only, not physical
   database proof. Credentials and DSNs never enter Git, argv, logs, or artifacts.
6. Real service identities must demonstrate DML allowed; DDL, TEMP, SET ROLE and
   Billing sensitive users reads denied. Cross-namespace tests require both real
   targets; do not infer them from namespace labels or different hashes.
7. Only after actual checks emit `dev-lane-seed/v1`, result `verified`, CMDB-derived
   instance_id, namespace, node_uid, cluster_uid, candidate_sha, ordered roles
   `[xc_accounts_rw, xc_billing_rw]`, schema_initialized true. Caller independently
   binds producer/run/artifact checksum. No boolean input can produce a receipt.

Rollback: stop before app secret publication on any failure; retain the new
namespace/PVC and diagnostic metadata for review. No DROP, reset, PVC deletion,
production fallback, review/default-user seed, or automatic reinitialization.
Stripe Sandbox absence blocks business acceptance independently of DB setup.

Trusted wrapper integration should reuse the existing Dev Flux owner's authenticated
CMDB/SSH transport rather than introduce another Vault identity. Set
`dev_web_saas_seed_report` to the caller's temporary evidence path to collect the
public receipt locally. Same-run artifact contract: `dev-lane-seed-<stage>-<phase>-<run_id>-<attempt>`
containing `receipt.json`; only initialize can emit `dev-lane-seed/v1 verified`.
The seed-admin output is preparatory and cannot satisfy Flux verify. The caller
must authenticate artifact producer/source/run/attempt before passing the receipt.

The PostgreSQL candidate is an OCI index. The reviewed linux/amd64 child digest
is `sha256:f326b029499b2f255f5341d0a06d710c4380e6683315c8ab367b25c7069bdfca`
under root `sha256:c6ee94d4c91054128823e816e936e03702eee80fdcc04d3271a7e68167974ad2`.
The executor requires an amd64 node and accepts only that exact child runtime ID
(or the exact root pullable ID), never arbitrary digest suffixes. The mapping was
verified from OCI bytes/config/source labels in the parent acceptance evidence.
Unauthenticated fixed-SHA raw GitHub GETs for both source repositories were checked
HTTP200 with matching native/Billing SQL hashes before execution. Runtime fetch/hash
checks precede role/schema/Secret writes; no GitHub token enters a namespace.

The independent `xc_lane_audit` LOGIN role has only CONNECT, schema USAGE and
SELECT on `dev_lane_schema_status` (version/dirty metadata). Its random DSN is in
`web-saas-db-audit`, never business Pods. No users/password/MFA or business-table
SELECT is granted. PUBLIC execution on public functions and future owner functions
is revoked; only Accounts receives native invoker-function execution. Unexpected
public SECURITY DEFINER functions block initialization pending review. Authenticated
SCRAM audit probes require metadata reads and SQLSTATE42501 denial of business DML,
schema-version mutation, DDL, TEMP, owner/business SET ROLE and sensitive user reads.
The compatible receipt retains only the two business roles in database_roles and
records audit_role plus separate audit allow/deny flags. Actual cross-namespace
and K3s evidence remains required; local probes do not establish live acceptance.


Reusable CI entry: `.github/workflows/dev-web-saas-seed.yml` (workflow_call only),
fixed owner checkout `6e75ab1a195c8b8c751897449c9dadfdf288b91d` including the shared
strict SSH/session helper from C. Inputs are the same-run API-verified candidate
artifact/checksum, CMDB artifact/checksum, stage/phase, GitOps/platform SHA,
instance_id,node_uid,cluster_uid. Parent must review and authorize the exact new
job_workflow_ref allowlist entry before any operational use; this PR does not
change Vault roles/policies. The trusted Toolkit caller remains
`dev-feature-deploy.yml` on protected main. Seed-admin is not a verified seed.
