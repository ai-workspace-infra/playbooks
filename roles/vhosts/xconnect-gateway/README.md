# xconnect-gateway

Bootstraps the XConnect-One GatewayProvider without replacing the working
`xworkmate_bridge_distributed_vpn` role. Shadow remains the default; Batch 05
adds an explicitly gated Linux apply mode with a transactional runtime.

## Safety model

- `xconnect_gateway_enabled` defaults to `false`; installation does not start a
  not-yet-provided Agent binary.
- shadow and apply flags are mutually exclusive; apply must be explicitly set.
- `proxy_core` and the Relay backend are fixed to Xray-core v1.
- candidate, evidence and last-known-good state use separate directories.
- empty peer snapshots require an explicit signed `safety.allow_empty_peers`
  override; generations must advance `expected_previous_generation`.
- candidate config and provider manifests are validated before promotion.
- the legacy `wg-xwm`, `wg-quick@wg-xwm`, `xray-wg-tproxy` and
  `wireguard-over-vless.json` topology remains the M0-M2 rollback path. Its only
  compatibility change removes the Xray 26.3.27-deleted `allowInsecure: false`.
- shadow receives config/CLI `mode: shadow` and empty capabilities. Apply must
  receive matching config/CLI flags and only `CAP_NET_ADMIN`.
- loopback health must prove the selected mode and runtime-apply flag before a
  release is accepted as healthy.

## Enable a shadow node

Supply the signed Agent binary and public control-plane inputs through inventory
or an encrypted secret source:

```yaml
xconnect_gateway_enabled: true
xconnect_gateway_agent_install_method: url
xconnect_gateway_agent_binary_url: https://downloads.example.net/xconnect-gateway-agent-0.1.0
xconnect_gateway_agent_binary_checksum: sha256:<64 lowercase hex characters>
xconnect_gateway_node_id: jp-gateway-01
xconnect_gateway_control_plane_url: https://accounts.svc.plus
xconnect_gateway_snapshot_signing_public_key: "<base64-encoded 32-byte Ed25519 public key>"
xconnect_gateway_snapshot_signing_key_id: signing_key_01
xconnect_gateway_manage_credentials: true
xconnect_gateway_credentials_source: /secure/controller/path/node-credential.token
```

`preinstalled`, `local`, and `url` install methods are supported. Managed local
and URL releases require a sha256 checksum and are staged in a versioned release
directory before the stable symlink changes. The canonical, publishable source
is `tools/xconnect-gateway-agent`; CI verifies it with the race detector and
cross-builds Linux amd64/arm64 without committing binaries. The role consumes a
release artifact and checksum produced from that module.

The Agent uses the internal Controller boundary under
`/api/internal/overlay/v1/nodes`. These endpoints remain an explicit accounts
control-plane dependency; the Agent's `httptest` contract coverage is not a
claim of live end-to-end availability. Its checkpoint records
`observed_generation` separately from `applied_generation` (always zero in
shadow mode), and retries a durably queued result without re-observing an
identical snapshot.

The host must already have the pinned Xray-core binary. The role intentionally
does not download a mutable `latest` release. Run:

```bash
ansible-playbook -i inventory.ini xconnect-gateway.yml --syntax-check
scripts/validate-xconnect-gateway-role.sh
```

## Rollback

Disable `xconnect_gateway_enabled` and stop `xconnect-gateway-agent`. Failed
service or health verification restores the previous config, provider manifest,
systemd unit, managed node credential, signing public key, and managed release
symlink before restoring the prior service state. Identical deployments ensure
the service is started and healthy without restarting it. Shadow mode never
alters `wg-xwm`, `xray-wg-tproxy`, client peers, routes, or ACLs. Apply mode
performs the explicit handoff below while preserving both legacy configs as the
M0-M2 rollback source.

## Explicit runtime apply (Batch 05)

Runtime mutation remains off by default. Set `xconnect_gateway_shadow_mode:
false` and `xconnect_gateway_runtime_apply_enabled: true` together to opt a
Linux Gateway into the signed transaction path. The role then grants only
`CAP_NET_ADMIN`; shadow mode retains an empty capability set.

Before first activation, provide these protected remote bootstrap sources:

```yaml
xconnect_gateway_wireguard_target_config_source: /root/xconnect-seed/wg-xco.conf
xconnect_gateway_xray_runtime_baseline_source: /root/xconnect-seed/xray-add-inbound.json
xconnect_gateway_relay_credential_source: /root/xconnect-seed/relay-credential.json
xconnect_gateway_relay_tls_certificate_source: /root/xconnect-seed/relay.crt
xconnect_gateway_relay_tls_private_key_source: /root/xconnect-seed/relay.key
```

The WireGuard source must be root-owned 0400/0600 and is copied without exposing
its private key to the Agent or logs. An already provisioned
`/etc/wireguard/wg-xco.conf` is also accepted if it is a root-owned 0400/0600
regular file. The Xray baseline must be a protected `AddInbound` JSON for the
dedicated `xconnect-one-relay` tag. The relay credential must be strict JSON
containing the node UUID and the configured absolute TLS paths. The role copies
the credential, certificate and key into dedicated-user-owned 0600 files, then
runs both the contract validator and `xray run -test` as that unprivileged user;
a root-only false-positive preflight is therefore rejected. These `force:
false` files are first-activation seeds, not the later credential rotation API.

The role captures active/enabled state for legacy `wg-quick@wg-xwm` and
`xray-wg-tproxy`, stops legacy WireGuard, starts `wg-quick@wg-xco`, and verifies
its exact canonical addresses and UDP listen port. It then stops legacy Xray,
starts the restricted `xconnect-one-xray`, starts the Agent, and waits for apply
health. Only after health succeeds are both legacy units disabled. Any failure
stops the target units, removes first-install seed files, and restores the
captured legacy active/enabled states. Legacy configs remain on disk for the
documented rollback.

The signed snapshot must exactly match this node's configured WireGuard
interface, listener, addresses, and Xray listener. Interface addresses are
read-only verified with the allowlisted `ip` binary before any mutation. The
transaction preflights everything, then applies the exclusive `inet
xconnect_one` forward table, the Xray inbound, and WireGuard peers. Rollback
reverses that order. It never captures or flushes the host ruleset and never
calls `sudo`, a shell, or arbitrary systemd units.

The Accounts policy endpoint and Xray HandlerService are deployment
prerequisites; fake and isolated-namespace tests do not claim production E2E.
Static group vars remain unchanged as the M0-M2 rollback source.

### Manual recovery from a rollback fault

`apply_failed_rollback_failed` is persistent and never auto-retries. If the
Agent can verify `ip link set dev wg-xco down`, health is `fail-closed`;
otherwise it reports `unsafe-manual-recovery`. In either case:

1. Stop `xconnect-gateway-agent` and keep the overlay interface isolated.
2. Inspect `runtime-transaction.json` and its 0700 transaction directory;
   restore `wireguard.previous`, `xray-runtime.previous.json`, and only the
   saved `inet xconnect_one` table. Never restore or flush the host ruleset.
3. Verify the intended WireGuard/Xray/nftables state, remove the resolved
   transaction journal, and explicitly bring `wg-xco` up.
4. While the service is still stopped, run
   `xconnect-gateway-agent --config /etc/xconnect/gateway/gateway.json --mode apply --clear-runtime-fault <snapshot_id>`.
   The acknowledgement refuses a remaining journal, a mismatched snapshot ID,
   or an interface that is not read back as UP. Then restart the Agent and
   confirm health before accepting new snapshots.
