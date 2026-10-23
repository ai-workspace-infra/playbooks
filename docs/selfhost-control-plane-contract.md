# Selfhost control plane owner contract v1

Owner: Playbooks. Caller: the `.github` install-control-plane thin dispatcher.
This file is the integration contract for the parallel GitOps/caller work.
The owner is supplied for Draft review; no live acceptance or production execution
is implied. All examples use fictional targets.

Integration note (2026-10-23): use the exact operational fields below in the
GitOps schema. Service repository keys are `accounts`, `billing-service`,
`portal`, `content-service`, `postgresql`; Compose keys are respectively
`accounts`, `billing`, `console`, `content`, `postgres`. The two operator ENV
paths are mandatory. The final shared v1 rejects `target.cmdb_file` and
`vars_files`; CMDB and reference policy are external operator files. The schema
followup adds optional `execution.storage` with the storage fields below.
Fresh SIT passes `sit` unchanged and requires the Accounts additive native-init
support in PR 267 commit `d3b6750` or its reviewed successor; older images fail.
This source support is not proof of a published/accepted SIT artifact.
Target aliases have no enforced relationship to generation labels. Image tags
may contain uppercase characters (for example `:RC1@sha256:...`).

```sh
PLAYBOOKS_CHECKOUT=/fixed/playbooks GITOPS_CHECKOUT=/fixed/gitops \
CONTROL_PLANE_CMDB=/protected/cmdb.json \
CONTROL_PLANE_VARS_FILE=/protected/runtime.yaml \
python3 /fixed/playbooks/scripts/control_plane/entrypoint.py \
  --config /fixed/gitops/control-planes/example.yaml \
  --gitops-ref <40-character-GitOps-SHA> [--check]
```

`--config` accepts an absolute file inside GitOps or a GitOps-root-relative path.
References inside that file resolve relative to its directory and must remain
inside the GitOps checkout, including symlinks. Only the two operator environment
variables above may refer outside it. GitOps HEAD must equal the argument and
both checkouts must be clean, including untracked files. The Playbooks HEAD must
equal `execution.playbooks_sha`. No fetching, package installation, SSH, Vault,
Ansible invocation or file creation occurs in `--check`; it emits a JSON plan.
The caller provisions Python 3.11+, PyYAML, Ansible and required collections.

## Declaration

```yaml
schema_version: 1
kind: selfhost-control-plane
environment: uat                 # sit | uat | prod; candidate is always isolated
source:                         # optional audit metadata; never an execution target
  host: old.example.invalid
  generation: old
target:
  host: candidate.example.invalid # exact CMDB inventory key, never an Ansible pattern
  generation: uat-next
  domain_base: example.invalid
  console_domain: console-selfhost-uat-next.example.invalid
  accounts_domain: accounts-selfhost-uat-next.example.invalid
release:
  snapshot_tag: uat-daily-build-YYYY.MM.DD-rN
  services:                     # exactly these five immutable image references
    accounts: registry.example/accounts@sha256:<64-hex>
    billing-service: registry.example/billing@sha256:<64-hex>
    portal: registry.example/console@sha256:<64-hex>
    content-service: registry.example/content@sha256:<64-hex>
    postgresql: registry.example/postgresql@sha256:<64-hex>
  accounts:
    source_sha: <40-hex>
    schema_sha256: <64-hex>
    billing_schema_sha256: <64-hex>
    migration_version: 2026102001
    business_tables: [users]     # exact sorted native-schema manifest table list
  billing:                      # optional schema metadata; execution requires it
    source_sha: <40-hex>
    schema_file: sql/billing-service-schema.sql
  doco_cd:
    image: ghcr.io/kimdre/doco-cd@sha256:<64-hex>
    repository: https://github.com/ai-workspace-infra/gitops.git
    target: uat-next-candidate   # actual .doco-cd.<target>.yml in pinned GitOps
    storage_target: uat-next-storage
execution:
  playbooks_sha: <40-hex>
  mode: candidate
  initialize: true              # explicit eligibility; never reset/reinitialize
  storage:                     # required when legacy CMDB has no storage profile
    profile: root              # bundled root disk is supported
    data_path: /data/postgresql
```

The snapshot tag identifies the coordinated five-service build; GitOps is pinned
separately by `--gitops-ref`. No corresponding GitOps tag is required. Each Doco-CD
manifest uses `version: doco.v1`, `working_dir`, `compose_files`, `env_files`,
`remove_orphans: false`. Those paths remain inside GitOps. All selected service
and dependency images must be digest pinned; the five core resolved images must
match `release.services`. No build, include, lifecycle hooks, privileged services
or host namespace modes. Candidate Accounts and Billing explicitly set
`DATABASE_BACKGROUND_WRITERS: "false"`; outbound SMTP/payment credentials are
absent. The storage selection contains only `postgres`. The application
selection includes all five core services (Compose key `postgres` maps to
contract key `postgresql`). Doco-CD selects the exact GitOps commit, not main.

Native host-config files use `/etc/xcontrol/web-saas/{secrets.env,config/*,certs/*}`.
Compose must reference those paths. Persistent PostgreSQL uses the CMDB storage
profile's `data_path` bound to `/var/lib/postgresql/data`; no anonymous database
volumes. Bundled root storage and dedicated mounted disks are both supported.
Only Caddy may publish public host ports. PostgreSQL, Accounts, Billing, Console,
Content and dependency telemetry may publish nothing or explicit IPv4/IPv6
loopback mappings only; implicit/wildcard/host-address mappings fail preflight.
This owner verifies storage and never formats or clears it. Fresh installation
means Docker + runtime config + PostgreSQL cluster + native schema + application
reconciliation; creating a cloud host and preparing its disk remain upstream.

## CMDB and runtime inputs

CMDB is consumed through `inventory/terraform_cmdb.py`, never generated here.
It must declare matching `environment` (or the exact environment in the target's
authoritative `groups` for legacy flat IaC CMDB), one exact target with `ip`/`ansible_host`,
explicit `ansible_user`, `instance_id`/`resource_id`, and `storage` (or
`host_vars.control_plane_storage`) containing `profile: root` and
`data_path: /data/postgresql`. A dedicated profile adds `mount_path` and
`device_path`; optional `filesystem_uuid` binds the filesystem. The existing
`data_disk` with mount_path/device_path is also accepted as a dedicated profile.
Root profiles must actually reside on the target root filesystem. Neither a
dedicated disk nor a `/dev/disk/by-id` naming scheme is mandatory. Target aliases
are explicit declaration metadata and are not inferred from generation names.
For the existing flat Akamai IaC CMDB without storage metadata,
`execution.storage` declares the nonsecret profile; it is verified on the actual
CMDB-selected host. This does not alter or supplement cloud resource facts.
`source.host`, when present, must differ from the target and may not alias its
CMDB address/resource identity. No source connection or source mutation occurs.
Verified manual temporary inventories allowed by AGENTS remain separate manual
entry points; this runner defaults exclusively to authoritative CMDB.

Runtime policy files are operator-owned regular files with no group or other
permissions (0600/0400). They contain **references only**, never tokens, DSNs,
passwords or private keys. The single policy file has one `control_plane_runtime` key.
No arbitrary Ansible extra-vars or target overrides are accepted. Example:

```yaml
control_plane_runtime:
  vault_addr: https://vault.example.invalid
  vault_token_env: VAULT_TOKEN
  secret_refs:
    POSTGRES_PASSWORD: kv/uat/databases#postgres_root_password
    ACCOUNT_PG_PASSWORD: kv/uat/databases#account_pg_password
    AUTH_TOKEN_PUBLIC_TOKEN: kv/uat/accounts/runtime#AUTH_TOKEN_PUBLIC_TOKEN
    AUTH_TOKEN_REFRESH_SECRET: kv/uat/accounts/runtime#AUTH_TOKEN_REFRESH_SECRET
    AUTH_TOKEN_ACCESS_SECRET: kv/uat/accounts/runtime#AUTH_TOKEN_ACCESS_SECRET
    INTERNAL_SERVICE_TOKEN: kv/uat/accounts/runtime#INTERNAL_SERVICE_TOKEN
    ca_cert_b64: kv/uat/control-plane/tls#ca_cert_b64
    server_cert_b64: kv/uat/control-plane/tls#server_cert_b64
    server_key_b64: kv/uat/control-plane/tls#server_key_b64
    tls_fullchain_b64: kv/uat/control-plane/tls#tls_fullchain_b64
    tls_key_b64: kv/uat/control-plane/tls#tls_key_b64
    registry_username: kv/uat/control-plane/registry#username
    registry_password: kv/uat/control-plane/registry#password
    # git_access_token: kv/uat/control-plane/git#token  # optional private GitOps
```

The paths above are illustrative references, not requests to create/copy Vault
records. Use the established environment-scoped owners. The database and shared
service-token references shown are canonical and enforced. Ansible resolves
references only at execution in a delegated `no_log` task using an already-issued
short-lived OIDC/Vault identity. Results remain in non-cacheable task memory;
no controller credential files are created. `--check` never authenticates to
Vault. Native configuration is rendered only on the target, with its existing
permission controls. Candidate mode forces managed schema and disabled background
writers, root bootstrap, SMTP, OAuth and Stripe. The existing business permission
owner supplies `account_user` grants and never rotates its canonical password.

Initialization requires `execution.initialize: true`, an absent or empty account
database, no application writers/reconciler and a matching compiled native schema
manifest. Existing initialized targets require this owner's matching receipt,
unchanged cluster/database identity and schema/checkpoint validation. The owner
never drops a database or clears a migration marker. Interrupted initialization
retains storage; ambiguous partial state stops for review.

First initialization also applies the reviewed Billing-owned bootstrap SQL before
business grants or application startup. Execution requires `BILLING_CHECKOUT`:
the canonical Billing clone at `release.billing.source_sha`, clean including
ignored/untracked files. The tracked `sql/billing-service-schema.sql` bytes must
match `release.accounts.billing_schema_sha256`; the runner never fetches or
changes the checkout. Only additive table/index DDL, empty business tables,
paused writers and a bounded transaction are eligible. No seed/catalog overwrite
or migration-marker rewrite. The Billing hash and post-DDL schema fingerprint
are journaled; identical reruns only verify. Existing targets without this
receipt require explicit backup/restore and a separate schema-upgrade gate.
`--check` remains a credential-free declaration plan, not execution acceptance.

## Migration owner

`selfhost_core_data_migration` is a separate reusable Role, never automatically
called by deployment. URL/API mode calls the reviewed Accounts client using
source/target HTTPS URLs and token environment names or Vault references.
Preview is default. Apply binds the original plan ID/hash and requires verified
target backup + isolated restore evidence. Unsupported API versions fail; no
legacy SSH/database fallback. The reviewed existing target-side legacy migration
entry points remain explicit, unchanged manual paths for the first V4 to V5 move.
The API contract is Accounts `docs/migrations/api-v1.md` (PR 267). The owner calls
the checksum-verified **existing** `migratectl transfer-api` command. It never
builds or substitutes an unpinned PATH executable:

```sh
<verified-client> transfer-api --source-url https://source.example.invalid \
  --source-token-env SOURCE_MIGRATION_TOKEN \
  --target-url https://target.example.invalid --target-token-env TARGET_MIGRATION_TOKEN
<verified-client> transfer-api --target-url https://target.example.invalid \
  --target-token-env TARGET_MIGRATION_TOKEN --status --plan-id <original-UUID>
<verified-client> transfer-api --target-url https://target.example.invalid \
  --target-token-env TARGET_MIGRATION_TOKEN --apply \
  --plan-id <original-UUID> --plan-sha256 <reviewed-64-hex>
```

`--source-auth-kv` and `--target-auth-kv` are the mutually exclusive Vault-reference
alternatives. Only literal scopes `source.read`, `target.plan`, `target.apply`
are supported; each token remains bound by Accounts to the same subject/pair/job.
Preview retries use `--snapshot-id`/`--plan-id`. The Playbooks command wrapper
`scripts/control_plane/migration.py` accepts a nonsecret JSON request on stdin:
`policy` (the GitOps accounts-migration declaration), `client` (absolute reviewed
binary), `client_sha256`, optional `action` (preview default, status, apply),
`plan_id`, `snapshot_id`, `plan_sha256`, and `check` (no API/Vault calls).
Apply additionally needs authenticated owner evidence references:
`backup_receipt`, `backup_receipt_sha256`, `owner_proof`, `owner_proof_sha256`,
`owner_sha`; optional `backup_archive` is an encrypted artifact, never plaintext.
The reusable `selfhost_core_data_migration` Role invokes that same wrapper.

The separate `selfhost_core_data_backup` Role calls the existing encrypted backup
and independent isolated-restore owners on the exact CMDB target, then performs
a fresh API preview with the original immutable source snapshot while target
writers remain paused. It verifies full schema, native row/sequence equality,
actual encrypted archive checksum, physical database identity and unchanged
canonical API target baseline before issuing the API's exact receipt fields:
`format`, `backup_id`, `backup_sha256`, `target_identity_sha256`,
`target_schema_sha256`, `target_baseline_sha256`, `restored_baseline_sha256`,
`restore_verified`, `verified_at`, `expires_at`. No native hash is relabeled as
an API hash. See the Role READMEs for inputs, authenticating producer/run evidence,
and target-only file placement. Unchanged committed retries return authenticated
subject-bound status with the exact hash before obsolete backup or plan expiry
checks; they never submit another writable call.

Accounts integration consumes only these CLI/JSON contracts: plan ID/hash/state,
snapshot ID/hash, target identity/schema/baseline hashes, `tool_sha256`, conflict
count and expiry. It imports no Go structs or Accounts domain implementation.
Adding authenticated chunk metadata to manifests is compatible if those fields
remain stable. Chunk/page capture and resume remain an Accounts producer task;
the bounded v1 limit is not declared complete by this owner.

## Candidate readiness and remaining acceptance gates

Installation completion requires the actual five pinned images plus actual
Console HTML, Accounts/Billing database `/readyz`, Content health and an
authenticated Content read endpoint, Caddy configuration, trust-verified
Console/Accounts HTTPS SNI, and the running Doco-CD image/poll selection.
The new candidate-specific probe is used because the legacy diagnostics use
insecure TLS and assume additional production containers. Raw HTTP bodies,
container environments and credentials never enter evidence. A dedicated
Ansible callback emits only the sanitized owner receipt. No matched host or no
actual readiness receipt is an error, even when Ansible returned zero.

This is **candidate component readiness**, not completed business acceptance.
OAuth callbacks, payment/webhooks, authenticated user journeys, Agent connectivity
and billing ingestion observation remain explicit `not_run` gates requiring an
authorized sandbox. The opt-in listener, receipt-schema initialization and Caddy
route now have the separate `selfhost_migration_listener` owner; see
`docs/selfhost-migration-listener-contract.md`. Ordinary install never enables it.
Native initialization intentionally creates zero business rows. A default
billing catalog/bootstrap needs a separately reviewed authoritative seed owner;
it is not fabricated here. First empty initialization now applies the pinned
Billing DDL, but that never claims a paid/default catalog has been seeded.

## Owner verification

`python -m unittest discover -s tests/control_plane -v` runs local contract,
resolver, negative-path, pinned client, evidence adapter, real HTTP and real
Ansible rerun fixtures. Set `CONTROL_PLANE_GITOPS_SCHEMA` to the reviewed schema
to validate the same sample in both owners. The schema was checked against
GitOps PR 506 commit `2ae1311547acf5aab9d9b7cf927d166da4b37645`.
Set `CONTROL_PLANE_PUBLIC_INSTALLER` to the reviewed public wrapper to exercise
the actual wrapper -> real pinned entrypoint `--check` path; both checkouts
remain clean and no owner is mocked. No workspace path is baked into the test.
Set `CONTROL_PLANE_POSTGRES_FIXTURE=1` to run a local-network-disabled,
tmpfs-backed disposable PostgreSQL 17 encrypted populated/empty restore and
tamper-refusal test. It accepts only a local Unix Docker endpoint and removes
only its own generated container. These results do not claim live acceptance.
PR CI runs the owner and PostgreSQL fixtures without Vault/cloud credentials.

The opt-in `test_api_restore_integration.py` now proves real Accounts API/native
DB -> encrypted backup -> isolated restore -> owner adapter -> gated apply ->
exact retry on empty and populated targets, using Accounts
`c5e3bae3bea1d9b71ee2a579e4da8657fc192bc8`. It reproduces the former stripped-ACL
defect (SQLSTATE42501), then passes with object owners/ACL/default grants preserved.
Actual account_user TCP SELECT/INSERT rollback probes, password/TOTP checks and
unchanged retry rows/xmin/sequences/one receipt are independent gates. An empty
restore remains G3 blocked; it is not relabeled populated recovery acceptance.
CI uses fixed Accounts/Billing/GitOps source pins and disposable local containers;
no host deployment or paid-catalog/traffic-cutover acceptance follows from this.

## Evidence and rollout

JSON plan: owner/gitops SHAs, snapshot tag, exact host and resource identity,
digest mapping, initialization intent, Doco-CD storage/application selection.
Execution verifies actual installed images and controller polling configuration;
it never claims authenticated business acceptance, DNS cutover or writer handoff.
Owner implementation -> reviewed immutable owner pin -> caller/GitOps update ->
isolated acceptance. Main owns the caller and GitOps changes. No legacy caller
deletion, release publication, deployment or production approval occurs in this PR.
