# Explicit target migration listener contract

Shared declaration schema/examples: GitOps Draft PR506 at
`9a297862779f3999daf8dd8afe8812fc4805df8e` (not merged). CLI/server source and
backup gate contract qualified at Accounts `c5e3bae3bea1d9b71ee2a579e4da8657fc192bc8`.

Owner: Playbooks `selfhost_migration_listener`. Caller: an explicitly authorized
candidate-only operation, never ordinary installation or a source/V4 deployment.
GitOps owns all Compose/Doco declarations. No live operation has been performed.

```sh
python3 "$PLAYBOOKS_CHECKOUT/scripts/control_plane/listener.py" \
  --config <GitOps-root-relative-listener.yaml> --gitops-ref <40SHA> [--check]
```

Use the same fixed clean `PLAYBOOKS_CHECKOUT`, `GITOPS_CHECKOUT`,
`CONTROL_PLANE_CMDB`, `CONTROL_PLANE_VARS_FILE` contract as ordinary deployment.
The listener reads the referenced ordinary control-plane config only to verify
immutable pins, CMDB identity, native storage and explicit candidate ownership;
it never invokes installation or initialization of the Accounts business schema.

Declaration: `schema_version: 1`, `kind: selfhost-migration-listener`,
`control_plane_config` (GitOps-root-relative ordinary candidate config),
root `doco_target` (separate combined target stack + listener overlay selection),
optional `example_only` (false accepted; true rejected before checkout/Vault/host
work), and `listener` with these exact nonsecret fields:

- `source_url`: exact approved remote source HTTPS origin, not a deployment target.
- `source_audience`, `target_audience`, `job_id`: explicit distinct pair/job binding.
- `allowed_peer_cidrs`: narrow exact source networks for outbound pulls.
- `allowed_client_cidrs`: narrow exact operator networks plus `127.0.0.1/32` for
  the namespace-local trusted SNI capability probe.
- `apply_enabled`, `initialize_receipts`: explicit booleans, normally false/false.
- `receipt_owner_role`: dedicated target migration owner, not postgres/account_user.
- `receipt_file`: required iff apply is eligible; existing canonical target-only
  owner receipt under /run or /data, never generated evidence in GitOps.

GitOps declares service `migration`, container `web-saas-migration-listener`,
same pinned Accounts digest and entrypoint `[/usr/local/bin/migratectl]`.
The exact target-only `command` is `arguments(listener)` in `listener.py`;
it relies on the reviewed CLI default loopback9097 plus `--allow-loopback-http`.
`network_mode: service:caddy` makes that loopback reachable only in Caddy's
namespace. No published ports, extra networks, privileged/device mounts or
alternate entrypoint. Require root UID, read-only root, dropped capabilities
and no-new-privileges. The role creates no Compose or alternate polling model.

Explicit mounts: private persistent `<parent-of-data_path>/migration-stage` to
`/private/target-stage`, plus optional target-only receipt at the same absolute
path read-only. The native target Caddy template imports
`/etc/caddy/migration-listener*.caddy` before its general read-only method guard.
GitOps must explicitly bind `/etc/xcontrol/migration-api/route.caddy` there.
The route matches exact Accounts Host AND migration API path AND allowed client
CIDRs; other clients receive 403. The rest of candidate ingress stays read-only.

## Runtime-only credentials and recovery

`CONTROL_PLANE_MIGRATION_VARS_FILE` is one protected reference-only YAML policy
with `vault_addr`, `vault_token_env`, `secret_refs` for
`TARGET_MIGRATION_DSN`, `TARGET_MIGRATION_SIGNING_KEY`,
`TARGET_MIGRATION_STAGING_KEY`. References must be
`kv/<env>/accounts/migration/<record>#<field>`. The DSN uses a dedicated migration
executor or the explicitly selected receipt owner during initialization; never
postgres/account_user/readonly_release. Signing/staging keys are distinct 32-byte
base64 values, not shared service JWTs. DSN passwords must be URI encoded.
Credentials are resolved only in delegated no_log task memory, with the selected
Vault identity explicitly mapped; no controller credential files. Target values
live only in root-private target `/etc/xcontrol/migration-api/listener.env`
(0700 directory, 0600 files). Required route/env mounts persist across reboot.
Each deployment refreshes Vault values and verifies actual container values;
there is no fallback to old files or ambient credentials when Vault fails.

For apply startup, protected external nonsecret JSON
`CONTROL_PLANE_MIGRATION_RECOVERY_FILE` supplies `backup_receipt`,
`backup_receipt_sha256`, `owner_proof`, `owner_proof_sha256`, `owner_sha`,
`plan_id`, `plan_sha256`, `backup_archive`. They must be authenticated producer/run
outputs. Paths refer only to existing target-local artifacts, not controller
copies. The owner checks root ownership and non-writable nonsymlink ancestry,
file/checksum equality, native backup/isolated-restore proof and actual encrypted
archive/physical DB identity through the existing receipt adapter. No fake gate.

Receipt schema DDL is only the explicit `initialize_receipts` operation, adding
`--initialize-receipts --receipt-owner-role` to the approved CLI using that exact
dedicated owner DSN. No implicit privilege grants/business-role fallback. Disable
that flag after initialization for ordinary executor restarts. Accounts retains
all transaction, live baseline, auth, expiry and committed-retry enforcement.

Changed route input is validated with the actual pinned Caddy image before a
changed-config target restart. Actual sidecar image, arguments, namespace and
environment are then checked. A namespace-local trust-verified HTTPS/SNI GET
must reach the API and return unauthenticated 401 (not a writable test). No
redirects, TLS bypass, token mint or source action. This is component readiness,
not paid-catalog, migration-apply or business acceptance.

## Schema and catalog separation

Fresh Accounts initialization now applies checksum-pinned Billing-owned DDL from
the fixed clean `BILLING_CHECKOUT`, journaled with the final schema fingerprint.
No catalog/default/business rows are seeded. An owned rerun verifies rather than
reapplies DDL. An existing target without the new Billing receipt stops before
configuration changes and requires a separate reviewed backup/schema upgrade.

Rollback selects the prior ordinary candidate Doco target; route removal is an
explicit reviewed target operation. Never delete staging, schema or backup proof
to retry. Main owns caller/GitOps pin updates; local tests are not live acceptance.
