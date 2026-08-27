# xconnect-gateway-agent

Shadow-only XConnect-One infrastructure data-plane Agent. It is an independent
Go module inside `playbooks`; no new top-level repository is required.

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

It contains no WireGuard or nftables mutation backend. Runtime apply belongs to
a later batch and requires a separate, explicitly privileged provider.

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
