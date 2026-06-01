# xworkmate_bridge_distributed_vpn

This role deploys the private transport used by the XWorkmate bridge distributed extension.

## Topology

The current implementation is a two-node `dual-node` topology:

- `jp-xhttp-contabo.svc.plus` is the primary node for `xworkmate-bridge.svc.plus`.
- `cn-xworkmate-bridge.svc.plus` is the CN edge node for `cn-xworkmate-bridge.svc.plus`.

Both nodes run the same private network path:

```text
WireGuard peer -> 127.0.0.1:51830 -> xray-wg-tproxy -> VLESS/TLS -> peer xray-wg-tproxy -> peer UDP 51820
```

The role intentionally does not manage the host's default `xray.service` or
`/usr/local/etc/xray/config.json`. WireGuard-over-VLESS uses its own config and
service:

- `/usr/local/etc/xray/wireguard-over-vless.json`
- `xray-wg-tproxy.service`

## Managed Services

Each node gets:

- WireGuard interface: `wg-xwm`
- WireGuard listen port: UDP `51820`
- local Xray dokodemo-door ingress: `127.0.0.1:51830`
- VLESS/TLS listen port: TCP `2443`
- VPN-only bridge forwarder: `<wg_ip>:8787 -> 127.0.0.1:8787`

Systemd units:

- `wg-quick@wg-xwm.service`
- `xray-wg-tproxy.service`
- `xworkmate-bridge-vpn-forwarder.service`

The WireGuard peer endpoint on both sides is local:

```ini
Endpoint = 127.0.0.1:51830
```

## Inventory And Variables

The inventory uses split bridge groups and one distributed parent group:

- `xworkmate_bridge`
- `cn_xworkmate_bridge`
- `xworkmate_bridge_distributed`

Shared topology and VPN variables live in
[`group_vars/xworkmate_bridge_distributed.yml`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/group_vars/xworkmate_bridge_distributed.yml).

Host-specific distributed bridge behavior lives in:

- [`host_vars/jp-xhttp-contabo.svc.plus/xworkmate_bridge_distributed.yml`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/host_vars/jp-xhttp-contabo.svc.plus/xworkmate_bridge_distributed.yml)
- [`host_vars/cn-xworkmate-bridge.svc.plus.yml`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/host_vars/cn-xworkmate-bridge.svc.plus.yml)

Important defaults:

```yaml
xworkmate_bridge_distributed_vpn_interface: wg-xwm
xworkmate_bridge_distributed_vpn_wireguard_port: 51820
xworkmate_bridge_distributed_vpn_local_tproxy_port: 51830
xworkmate_bridge_distributed_vpn_vless_port: 2443
xworkmate_bridge_distributed_vpn_forwarder_port: 8787
```

## Secrets

This role reads secrets from the Vault service, not from a local Ansible Vault
password file.

Required controller environment:

```bash
export VAULT_SERVER_URL=https://vault.svc.plus
export VAULT_SERVER_ROOT_ACCESS_TOKEN=...
```

`VAULT_TOKEN` is also accepted when `VAULT_SERVER_ROOT_ACCESS_TOKEN` is not set.
Do not commit Vault tokens, WireGuard private keys, or the shared Xray UUID.

Vault KV base path:

```text
kv/xworkmate-bridge/distributed/wireguard-over-vless
```

Expected secret layout:

```text
common
  xray_uuid
hosts/<inventory_hostname>
  wireguard_private_key
```

The Xray UUID is the shared management-side UUID for this bridge transport. It
is not derived from tenant accounts or Xray account sync.

## Bridge Forwarding

The VPN forwarder exposes each bridge only on the WireGuard address:

- primary: `172.29.10.1:8787 -> 127.0.0.1:8787`
- CN edge: `172.29.10.2:8787 -> 127.0.0.1:8787`

Distributed task forwarding is configured through bridge topology. CN sets
`task_forward_peer_id: xworkmate-bridge`, so the bridge resolves the primary
private endpoint from `xworkmate_bridge_distributed_nodes`:

```text
http://172.29.10.1:8787
```

The primary node leaves `task_forward_peer_id` empty. That keeps the reverse
WireGuard/VLESS path available for private network reachability without sending
primary runtime tasks back to CN.

Both sides use the same `BRIDGE_AUTH_TOKEN`. CN does not configure a separate
forwarding token; an empty forwarding token means the bridge reuses its local
auth token.

## Deploy

Run from the playbooks repo:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export VAULT_SERVER_URL=https://vault.svc.plus
export VAULT_SERVER_ROOT_ACCESS_TOKEN=...

ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml --check --diff
ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml -f 1
```

Use `-f 1` for this two-host path when long SSH control sessions are unstable.

## Verification

On both hosts:

```bash
systemctl is-active xray-wg-tproxy wg-quick@wg-xwm xworkmate-bridge-vpn-forwarder xworkmate-bridge
xray run -test -config /usr/local/etc/xray/wireguard-over-vless.json
wg show wg-xwm
```

From the primary node:

```bash
ping -c 3 172.29.10.2
curl -H "Authorization: Bearer $BRIDGE_AUTH_TOKEN" http://172.29.10.2:8787/api/ping
```

From the CN edge node:

```bash
ping -c 3 172.29.10.1
curl -H "Authorization: Bearer $BRIDGE_AUTH_TOKEN" http://172.29.10.1:8787/api/ping
```

Regression checks:

- the primary host's `xray.service` still starts the original `/usr/local/etc/xray/config.json`
- both public bridge HTTPS endpoints still return `/api/ping`
- CN task forwarding resolves to the private `http://172.29.10.1:8787` endpoint
