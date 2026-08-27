# Static client migration to accounts projections

Batch 04 implements migration stages M0 through M2 without changing the live
WireGuard or Xray data plane. The static Ansible list remains the runtime source
while operators create and compare an accounts projection in shadow mode.

## Safety boundary

- `xconnect-static-import import` is a dry-run unless `--apply` is present.
- apply accepts only HTTPS, reads a protected accounts service-token file for
  the request, and sends `X-Service-Token` plus a deterministic
  `Idempotency-Key`.
- service tokens, response bodies, private keys, and transport credentials are
  never printed or copied into the document.
- only `id`, `wg_ip`, `public_key`, `attach_to`, and optional `tags` are accepted
  below `xworkmate_bridge_distributed_vpn_clients`.
- when `attach_to` is omitted, the importer expands the legacy role's default
  to every key in `xworkmate_bridge_distributed_vpn_nodes` before hashing.
- the tool never writes a client or peer back to `group_vars`, never invokes
  Ansible, and has no WireGuard, nftables, or Gateway runtime-apply backend.

The planned accounts endpoint is
`POST /api/internal/overlay/v1/imports/static-clients`. Until accounts exposes
that endpoint, only dry-run and local shadow comparison are supported; fixture
tests do not claim a live end-to-end import.

## M0: freeze and record the baseline

Build the operator tool, export the normalized import document, and commit the
reviewed evidence through the normal secure operations repository:

```bash
make -C tools/xconnect-gateway-agent build-static-import
tools/xconnect-gateway-agent/bin/xconnect-static-import import \
  --input "$PWD/group_vars/xworkmate_bridge_distributed.yml" \
  --network-id legacy-private \
  --owner-user-id 11111111-1111-4111-8111-111111111111 \
  --output /tmp/xconnect-static-import.json
```

The output is deterministic: devices, attachments, tags, and addresses are
sorted, and `source.baseline_sha256` hashes the normalized device projection.
Changing YAML comments or ordering does not change the baseline hash.
Replace the example `--owner-user-id` with the existing accounts UUID that owns
all imported v1 devices; the command rejects non-canonical UUIDs.

Review that every device contains only:

- `device_id`;
- the document-level `owner_user_id` shared by all v1 devices;
- `wireguard_public_key`;
- one host `/32` in `addresses`;
- migration/operator `tags`;
- the inventory Gateway identities in `attachments`.

## M1: controlled import

Do not use apply until the accounts endpoint, authorization policy, audit
events, and rollback API are deployed. When they are available, use a
short-lived operator credential and the already-reviewed document inputs:

```bash
tools/xconnect-gateway-agent/bin/xconnect-static-import import \
  --input "$PWD/group_vars/xworkmate_bridge_distributed.yml" \
  --network-id legacy-private \
  --owner-user-id 11111111-1111-4111-8111-111111111111 \
  --output /tmp/xconnect-static-import.json \
  --apply \
  --controller-url https://accounts.svc.plus \
  --service-token-file /secure/run/xconnect-import.service-token
```

Repeating the exact operation uses the same idempotency key. The POST body is
compact canonical JSON in field order `schema_version`, `kind`, `network_id`,
`owner_user_id`, `source`, `devices`; the key is
`sha256-<hex(sha256(canonical body bytes))>`. Any normalized document change
produces a different key and requires a new review. Import v1 intentionally
assigns every device to one owner; multi-owner mapping requires schema v2.

## M2: static versus projected shadow diff

Export a signed GatewaySnapshot through the control-plane operations path, then
compare the snapshot with one `attach_to` inventory identity:

```bash
tools/xconnect-gateway-agent/bin/xconnect-static-import diff \
  --input "$PWD/group_vars/xworkmate_bridge_distributed.yml" \
  --snapshot /secure/evidence/gateway-snapshot.json \
  --attachment jp-xhttp-contabo.svc.plus \
  --evidence /tmp/xconnect-static-shadow-diff.json
```

The evidence categorizes missing/unexpected device IDs, public-key fingerprint
mismatches, and allowed-IP mismatches. It does not contain raw keys.

The diff command strictly decodes the snapshot and validates its peer-set
shape, Xray-only marker, identity, and generation, but it is not a signature
trust decision. Feed it only a snapshot already verified by the Gateway Agent
or the control-plane evidence pipeline; signature and expiry rejection remains
the Agent's responsibility.

Exit codes are stable for automation:

- `0`: equal;
- `2`: invalid or unsafe input;
- `3`: valid inputs with projection drift;
- `1`: local I/O or Controller failure.

CI must require exit `0` for every Gateway attachment before any later dynamic
apply feature is enabled.

## Rollback and stop conditions

Batch 04 does not remove the static list and does not enable Gateway apply. If
an import or projection review fails:

1. stop importing and retain the generated baseline/evidence;
2. keep `xconnect_gateway_shadow_mode: true` and runtime apply disabled;
3. disable the accounts projection feature flag or quarantine the imported
   network through the control-plane rollback mechanism;
4. continue operating the existing static Ansible data plane;
5. correct accounts records, generate a new snapshot, and require an equal
   shadow diff before resuming migration.

Never regenerate `group_vars` from accounts. The only forward path after M2 is
for accounts to become authoritative and for a separately reviewed Gateway
runtime-apply batch to consume its signed snapshot.
