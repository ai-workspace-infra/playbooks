# xconnect-gateway

Bootstraps the XConnect-One GatewayProvider without replacing the working
`xworkmate_bridge_distributed_vpn` role. Batch 02 remains deliberately shadow-only:
the Agent may fetch, validate, render and compare signed snapshots, but its
provider manifest denies runtime apply and grants no Linux capabilities.

## Safety model

- `xconnect_gateway_enabled` defaults to `false`; installation does not start a
  not-yet-provided Agent binary.
- `xconnect_gateway_shadow_mode` must remain `true`.
- `proxy_core` and the Relay backend are fixed to Xray-core v1.
- candidate, evidence and last-known-good state use separate directories.
- empty peer snapshots require an explicit signed `safety.allow_empty_peers`
  override; generations must advance `expected_previous_generation`.
- candidate config and provider manifests are validated before promotion.
- the legacy `wg-xwm`, `wg-quick@wg-xwm`, `xray-wg-tproxy` and
  `wireguard-over-vless.json` assets are read-only bootstrap inputs; the legacy
  role and its systemd units are untouched.
- the Agent always receives both config `mode: shadow` and `--mode shadow`; its
  systemd capability sets are empty, so it cannot apply WireGuard or nftables.
- the loopback health response must prove `runtime_apply_enabled: false` before
  a release is accepted as healthy.

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
xconnect_gateway_snapshot_signing_public_key: "<Ed25519 public key>"
xconnect_gateway_manage_credentials: true
xconnect_gateway_credentials_source: /secure/controller/path/node-credential.token
```

`preinstalled`, `local`, and `url` install methods are supported. Managed local
and URL releases require a sha256 checksum and are staged in a versioned release
directory before the stable symlink changes. This repository intentionally does
not fabricate an Agent executable; its Go source/release pipeline remains an
external Batch dependency until a canonical source owner is established.

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
the service is started and healthy without restarting it. The role does not alter `wg-xwm`,
`xray-wg-tproxy`, client peers, routes or ACLs in this batch, so the existing
data plane continues to use its static configuration.
