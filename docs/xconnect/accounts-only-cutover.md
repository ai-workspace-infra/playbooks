# Accounts-only cutover gates

Batch 06 adds the M3 authority switch without deleting or rewriting
`xworkmate_bridge_distributed_vpn_clients`. The static inventory remains the
reviewed rollback reference, but the `xconnect-gateway` role never consumes it
to render a dynamic peer once accounts-only is selected. Gateway Agent remains
the only dynamic WireGuard, nftables, and Xray projection path.

## Evidence boundary

There is deliberately no new live "readiness" API. Operators assemble a
protected bundle from existing Accounts and Gateway boundaries:

- the static import response fields `import_id`, `idempotency_key`,
  `owner_user_id`, `network_id`, `baseline_sha256`, `device_count`, and
  `created_at`;
- the normalized imported devices and the node-bound Accounts projection;
- the Ed25519-signed GatewaySnapshot and exact compact policy artifact bytes;
- a Controller-signed Ed25519 authorization for `accounts-only` at the same
  generation;
- reconcile evidence where `processed == completed + failed`, `failed == 0`,
  and the operator has established `pending == 0`;
- the node heartbeat, successful apply-result, protected checkpoint, runtime
  peer/policy readback, and consecutive health samples.

The verifier strictly decodes the bundle, rejects trailing or unknown fields,
recomputes the import idempotency and projection hashes, checks every device ID,
public key, and IPv4 `/32`, verifies the snapshot signature and policy digest,
and requires `observed_generation == applied_generation` everywhere. Empty
projections, pending apply results, reconcile work, rollback faults, quarantine,
stale authorization, unavailable diffs, or one unhealthy sample reject cutover.

The authorization is not a local boolean. Its canonical signing bytes bind
`schema_version`, `kind`, `requested_mode`, `node_id`, `network_id`,
`generation`, `snapshot_id`, `baseline_sha256`, `projection_sha256`,
`policy_sha256`, reconcile counters, `issued_at`, and `expires_at`; `signature`
is excluded. The verifier pins a separate root-protected Accounts public key
and key ID. Editing the local Accounts projection, reconcile counters, digest,
generation, mode, or validity window invalidates the authorization.

Accounts does not yet publish this signed authorization producer/endpoint. That
is a required control-plane dependency before production accounts-only rollout;
operators must not replace it with an unsigned JSON attestation. The Batch06
mock signs the same canonical bytes solely to exercise the consumer contract.

The accepted JSON shape is documented by
`roles/vhosts/xconnect-gateway/files/contracts/accounts-only-readiness-evidence.schema.json`.
It contains hashes, identifiers, counts and check statuses, not raw tokens,
private keys, relay UUIDs, or TLS material.

## Explicit cutover

Install the separately built verifier beside the Agent and create the protected
bundle through the reviewed operations pipeline. Then enable all three flags:

```yaml
xconnect_gateway_enabled: true
xconnect_gateway_shadow_mode: false
xconnect_gateway_runtime_apply_enabled: true
xconnect_gateway_accounts_only_enabled: true
xconnect_gateway_accounts_only_readiness_binary: /usr/local/bin/xconnect-cutover-readiness
xconnect_gateway_accounts_only_readiness_bundle: /secure/evidence/accounts-only-bundle.json
xconnect_gateway_accounts_only_minimum_health_samples: 3
xconnect_gateway_accounts_only_authorization_public_key_path: /etc/xconnect/gateway/cutover-authorization-signing.pub
xconnect_gateway_accounts_only_authorization_key_id: cutover_key_01
```

The role runs the verifier before package installation, service takeover, or
any other deployment mutation. It uses the already-pinned snapshot signing key
and writes root-owned 0600 evidence. A nonzero verifier result stops the role.
The ordinary Agent config and health endpoint then expose
`projection_source: accounts-only`; health must match before promotion is
accepted.

Do not remove the static group vars at M3. Do not generate them from Accounts.
The role contains a CI guard that rejects any operational task/template that
references the static client variable or `group_vars`.

## Immediate stop and shadow/LKG fallback

To stop Accounts authority without restoring stale credentials or keys, set:

```yaml
xconnect_gateway_accounts_only_enabled: false
xconnect_gateway_shadow_mode: true
xconnect_gateway_runtime_apply_enabled: false
```

Re-run the role. The Agent returns to read-only shadow mode and reports
`projection_source: static-shadow`; the systemd unit has no Xray dependency in
shadow mode. The role leaves the dedicated Xray/WireGuard runtime and its
protected last-known-good files untouched. It does **not** start the legacy
services, copy the old `wg-xwm` private key, restore old VLESS UUIDs, or render
static peers automatically. An operator may separately execute the documented
Batch05 legacy handback only after reviewing those old credentials.

Runtime fault or quarantine is not an automatic rollback signal. Follow the
Batch05 manual recovery procedure first, preserve the journal, and acknowledge
the fault only after readback proves a safe LKG.

## Test scope

`scripts/test-xconnect-cutover-integration.sh` runs the signed bundle matrix, a
TLS `httptest` mock, policy/snapshot/apply-result checks, credential/TLS
permission tests, rollback/quarantine tests, the Ansible takeover contract, and
the isolated network namespace test. The namespace safely skips on macOS and
runs on Ubuntu CI. These are executable integration contracts, not evidence of
a deployed Accounts/Gateway E2E.
