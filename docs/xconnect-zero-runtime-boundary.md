# XConnect Zero Trust runtime-role boundary

`playbooks` owns host-level convergence only. It installs and operates the
Linux runtime used by the XConnect Zero Trust data plane; it does not define
cloud resources, environment topology, release pins, enrollment state, or
credentials.

## Canonical roles

| Data-plane function | Role |
| --- | --- |
| Gateway VLESS relay | `roles/vhosts/vpn-overlay/xray/hub` |
| Gateway WireGuard endpoint | `roles/vhosts/vpn-overlay/wireguard/hub` |
| One controlled-client VLESS transport | `roles/vhosts/vpn-overlay/xray/tproxy` |
| One controlled-client WireGuard endpoint | `roles/vhosts/vpn-overlay/wireguard/site` |

The XConnect UAT flow uses these roles as the Linux runtime baseline for
WireGuard over VLESS. XConnect Gateway and XConnect One obtain their active
configuration from signed XConnect Zero Accounts projections; the role must
not become a second source of device, network, policy, or secret state.

## Explicit non-ownership

- Terraform modules are owned by `iac_modules/vpn-overlay`.
- Public UAT topology and immutable artifact pins are owned by
  `gitops/vpn-overlay`.
- Vault owns enrollment credentials, VLESS credentials, signing material, and
  device private keys. They must not be committed in inventories, templates,
  or role defaults.
- `wireguard-gateway` and `wireguard-client` remain legacy generic roles.
  New XConnect Zero Trust work uses the canonical `vpn-overlay` roles above
  and must not add parallel service templates.
