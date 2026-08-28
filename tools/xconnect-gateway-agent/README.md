# xconnect-gateway-agent

XConnect-One infrastructure data-plane Agent and rollout utilities. They are an
independent Go module inside `playbooks`; no new top-level repository is
required.

The Agent:

- reloads the short-lived bearer credential file for every Controller request;
- posts a versioned heartbeat, fetches one planned GatewaySnapshot, and reports
  a shadow apply result through the `Controller` interface;
- verifies the Ed25519 signature and key ID, expiry, node identity, generation,
  replay transition, Xray-only runtime, peer-removal and empty-peer safety;
- invokes exactly `wg show <interface> dump` through an injectable runner;
- records only peer counts and route-difference counts, never WireGuard keys;
- atomically stores candidate, last-known-good, checkpoint and evidence files
  with `0700` directories and `0600` files;
- exposes loopback health with `runtime_apply_enabled: false`.

Runtime apply is explicit and transactional. Shadow remains the default and has
no network mutation capability. Accounts-only authority is a separate rollout
decision and is never inferred from runtime apply alone.

## Controller dependency

The following planned v1 endpoints are intentionally isolated in
`HTTPController` and exercised with `httptest`:

- `POST /api/internal/overlay/v1/nodes/heartbeat`
- `GET /api/internal/overlay/v1/nodes/{node_id}/snapshot`
- `POST /api/internal/overlay/v1/nodes/{node_id}/apply-result`

`accounts.svc.plus` does not yet expose these three endpoints. This module does
not claim a live Controller E2E until that dependency lands. A `204` planned
snapshot response means no new snapshot. The last route retains the product
contract's `apply-result` name, but shadow payloads always carry
`runtime_applied: false`, `applied_generation: 0`, and a separate
`observed_generation`.

GatewaySnapshot signing bytes use the same rule as the control-plane
SignedConfig: deterministic JSON with schema field order and the `signature`
field excluded. Field order is `schema_version`, `snapshot_id`, `node_id`,
`generation`, `expected_previous_generation`, `issued_at`, `expires_at`,
`proxy_core`, `safety`, `wireguard`, `relay`, then `policy`. The trust file
contains one base64-encoded 32-byte Ed25519
public key; configuration separately pins its `key_id`.

The checkpoint calls validated desired state `observed_generation`; it never
advances `applied_generation`. A pending shadow result is durably queued so a
Controller failure can be retried without re-observing or treating the same
signed snapshot as a replay.

## Build and verify

```bash
make -C tools/xconnect-gateway-agent check
make -C tools/xconnect-gateway-agent build
make -C tools/xconnect-gateway-agent build-cutover-readiness
scripts/validate-xconnect-gateway-agent.sh
```

No binary is committed. Release automation must inject `version` through
`-ldflags` and publish Linux amd64/arm64 artifacts with checksums for the
`xconnect-gateway` role.

## Static inventory migration

The same module publishes `xconnect-static-import`, a Batch 04 operator tool.
It strictly reads only `xworkmate_bridge_distributed_vpn_clients`, produces a
versioned and deterministic accounts import document, and defaults to dry-run.
It never renders dynamic peers back into Ansible. A separate `diff` operation
compares one inventory attachment with a GatewaySnapshot and emits redacted
JSON evidence. See `docs/xconnect/static-to-dynamic-migration.md` for the
migration and rollback procedure.

## Accounts-only readiness

`xconnect-cutover-readiness` consumes one protected, strict evidence bundle and
emits a redacted decision document. `--accounts-only` is mandatory. Readiness
requires the reviewed static-import hash and receipt, exact Accounts/static/
snapshot device projections, a valid Ed25519 GatewaySnapshot, the exact policy
artifact digest, Controller generation authorization, clean reconcile state,
matching Gateway heartbeat/apply-result/checkpoint/runtime readback, and a
configured number of consecutive healthy samples. Missing evidence returns
exit code `3`; malformed or unsafe input returns `2`.

Controller approval is a separate Ed25519-signed authorization, not a local
bundle boolean. It binds node/network/generation/snapshot, import baseline,
Accounts projection hash, policy digest, reconcile counters, mode, and validity
window. A pinned authorization public key is mandatory. Accounts still needs to
ship the production authorization producer; until then, the tool must not be
used to claim a live cutover approval.

The mock HTTPS handler under `internal/cutover` is a test-only composition
boundary. Accounts exposes the existing import and node APIs, not a synthetic
readiness endpoint; passing this harness is not a live E2E claim. See
`docs/xconnect/accounts-only-cutover.md`.
