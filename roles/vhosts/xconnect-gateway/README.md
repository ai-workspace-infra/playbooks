# xconnect-gateway

Bootstraps the XConnect-One GatewayProvider without replacing the working
`xworkmate_bridge_distributed_vpn` role. Batch 01 is deliberately shadow-only:
the Agent may fetch, validate, render and compare signed snapshots, but its
provider manifest denies runtime apply and grants no Linux capabilities.

## Safety model

- `xconnect_gateway_enabled` defaults to `false`; installation does not start a
  not-yet-provided Agent binary.
- `xconnect_gateway_shadow_mode` must remain `true`.
- `proxy_core` and the Relay backend are fixed to Xray-core v1.
- candidate, evidence and last-known-good state use separate directories.
- empty peer snapshots are rejected.
- candidate config and provider manifests are validated before promotion.
- the legacy `wg-xwm`, `wg-quick@wg-xwm`, `xray-wg-tproxy` and
  `wireguard-over-vless.json` assets are read-only bootstrap inputs; the legacy
  role and its systemd units are untouched.

## Enable a shadow node

Supply the signed Agent binary and public control-plane inputs through inventory
or an encrypted secret source:

```yaml
xconnect_gateway_enabled: true
xconnect_gateway_agent_binary_source: files/xconnect-gateway-agent
xconnect_gateway_node_id: jp-gateway-01
xconnect_gateway_control_plane_url: https://accounts.svc.plus
xconnect_gateway_snapshot_signing_public_key: "<Ed25519 public key>"
xconnect_gateway_control_plane_token: "<short-lived node token>"
```

The host must already have the pinned Xray-core binary. The role intentionally
does not download a mutable `latest` release. Run:

```bash
ansible-playbook -i inventory.ini xconnect-gateway.yml --syntax-check
scripts/validate-xconnect-gateway-role.sh
```

## Rollback

Disable `xconnect_gateway_enabled` and stop `xconnect-gateway-agent`. The role
does not alter `wg-xwm`, `xray-wg-tproxy`, client peers, routes or ACLs in this
batch, so the existing data plane continues to use its static configuration.
