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

Remote access clients are defined in
`xworkmate_bridge_distributed_vpn_clients`. Each client can set `attach_to` to
control which bridge nodes add the client public key as a direct WireGuard peer.
When a client attaches to only one node, the opposite node adds that client's
`/32` address to the inter-node peer `AllowedIPs` so return traffic routes
through the attached node instead of trying to contact the client directly.

For the productized overlay path, these client entries are the server-side
projection of the `accounts.svc.plus` overlay contract:

- `id` maps to `/api/overlay/devices/register` `device_id`
- `public_key` maps to `wireguard_public_key`
- `wg_ip` maps to the `/api/overlay/config` `wireguard.address` without the `/32`

The client-side CLI renders the matching WireGuard peer with
`Endpoint = 127.0.0.1:51830`; this role keeps the gateway-side peer list in
systemd-managed `/etc/wireguard/wg-xwm.conf`.

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

Current remote access clients:

```yaml
xworkmate_bridge_distributed_vpn_clients:
  - id: shenlan-macos
    wg_ip: 172.29.10.10
    attach_to:
      - jp-xhttp-contabo.svc.plus
      - cn-xworkmate-bridge.svc.plus
  - id: shenlan-ios
    wg_ip: 172.29.10.11
    attach_to:
      - jp-xhttp-contabo.svc.plus
```

`shenlan-ios` is intentionally single-attached to the primary node. XStream-VPN
on iOS owns the System VPN runtime and reaches the private bridge network
through the primary node; the CN edge reaches `172.29.10.11/32` through its
primary inter-node WireGuard peer.

The current static list is a bootstrap bridge. The next closure step is to let
the Go CLI or control plane export this list from `accounts.svc.plus` instead of
editing `group_vars` by hand.

The role validates this bootstrap client contract before rendering WireGuard:

- client `id` values must be present and unique
- client `wg_ip` values must be host IPv4 addresses, unique, and must not reuse a gateway IP
- client `public_key` values must look like WireGuard public keys
- `attach_to` must be non-empty when set and may only reference known VPN node inventory names

## Secrets

This role reads secrets from the Vault service, not from a local Ansible Vault
password file.

Required controller environment:

```bash
export VAULT_SERVER_URL=https://vault.svc.plus
export VAULT_SERVER_ROOT_ACCESS_TOKEN=...
export INTERNAL_SERVICE_TOKEN=...
```

`VAULT_TOKEN` is also accepted when `VAULT_SERVER_ROOT_ACCESS_TOKEN` is not set.
Do not commit Vault tokens, WireGuard private keys, or the shared Xray UUID.
The role posts the current gateway node to `accounts.svc.plus` through
`/api/internal/overlay/nodes/heartbeat`; this is required by default because
clients need the gateway `transport_uuid` before `/api/overlay/config` can be
issued. Set `ACCOUNTS_SERVICE_URL` when the accounts service is not
`https://accounts.svc.plus`. For an explicit offline/bootstrap deployment only,
set `xworkmate_bridge_distributed_vpn_sync_accounts_required: false`.
Before posting the heartbeat, the role derives the public key from the
Vault-provided WireGuard private key with `wg pubkey` and checks it against the
inventory public key. It also validates `common.xray_uuid` as a UUID. This keeps
`accounts.svc.plus` from publishing a gateway contract that clients can render
but cannot actually handshake with.
After the heartbeat returns, the role checks the returned `node` payload against
the deployed gateway facts: node id, network id, WireGuard public key/address,
endpoint host/port, `vless-tls` transport, `tls` security, UUID presence, and
healthy state. A successful deploy therefore proves the accounts control plane
accepted the same gateway contract the CLI will later sync.

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
`accounts.svc.plus` must persist the same value as the overlay node
`transport_uuid` through the internal heartbeat API, or expose it as
`OVERLAY_TRANSPORT_UUID` for local/bootstrap use; otherwise
`/api/overlay/config` must not issue client configs because VLESS
authentication would fail at the gateway.

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
export INTERNAL_SERVICE_TOKEN=...
export ACCOUNT_EMAIL=...
export ACCOUNT_PASSWORD=...
export BRIDGE_AUTH_TOKEN=...

ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml --check --diff
ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml -f 1
```

Use `-f 1` for this two-host path when long SSH control sessions are unstable.
For the full accounts + playbooks + local CLI closure, use:

```bash
scripts/verify-wireguard-over-vless-closure.sh
```

Recommended preflight and full run when using the adjacent repo `.env` files:

```bash
OVERLAY_ENV_FILES=.env,../accounts.svc.plus/.env \
OVERLAY_BUILD_BIN=1 \
OVERLAY_CHECK_ONLY=1 \
scripts/verify-wireguard-over-vless-closure.sh

OVERLAY_ENV_FILES=.env,../accounts.svc.plus/.env \
OVERLAY_BUILD_BIN=1 \
scripts/verify-wireguard-over-vless-closure.sh
```

`OVERLAY_CHECK_ONLY=1` is only a prerequisite check. It must not be treated as
closure evidence for account login, device registration, config delivery, local
runtime startup, private connectivity, or config ack. After the full run, verify
the resulting evidence directory:

```bash
scripts/check-wireguard-over-vless-closure-evidence.sh /tmp/wireguard-over-vless-closure-<utc timestamp>
```

The full closure script also runs this evidence checker before a successful
exit and writes the result to `closure-check.log`.

The closure script performs the product path in order:

1. `overlayctl login`
2. `overlayctl register-device`
3. `overlayctl sync-config`, `render`, and `preflight`
4. `overlayctl apply-playbooks-client`
5. `ansible-playbook vpn-wireguard-over-vless.yml`
6. `overlayctl sync-config`, `render`, and `preflight` again after gateway heartbeat
7. `overlayctl up`
8. `overlayctl check-connectivity --bearer "$BRIDGE_AUTH_TOKEN"`
9. `overlayctl ack-config`
10. optional `overlayctl down` when `OVERLAY_TEARDOWN=1`

Useful overrides:

- `ACCOUNTS_REPO`: defaults to `../accounts.svc.plus`
- `ACCOUNTS_SERVICE_URL`: defaults to `https://accounts.svc.plus`
- `OVERLAY_NODE_ID`: defaults to `xworkmate-bridge`
- `OVERLAY_ATTACH_TO`: comma-separated gateway inventory hosts
- `OVERLAY_REGISTER_ARGS`: optional explicit args for `register-device`; when
  unset, the script reuses `~/.xoverlay/session.json` public/private keys and
  only falls back to `--generate-key` when no local keypair exists
- `OVERLAY_STATE_FILE`: defaults to `~/.xoverlay/session.json`
- `OVERLAY_CONFIG_FILE`: defaults to `~/.xoverlay/overlay-config.json`
- `OVERLAY_EVIDENCE_DIR`: defaults to `/tmp/wireguard-over-vless-closure-<utc timestamp>`;
  every run writes `run.log`, `steps.log`, `closure-requirements.tsv`,
  `closure-verdict.env`, `summary.env`, `rerun.env`, tool versions, git status,
  and redacted overlay state/config snapshots there. Completed full-run evidence
  also includes `closure-check.log`. `summary.env` includes the selected
  paths, playbooks/accounts Git HEAD and dirty state, build output,
  `overlayctl` SHA256, per-requirement closure statuses, `closure_complete`,
  last recorded closure step/status, and any missing required paths, tools, or
  credentials. The local connectivity check and config ack are reported
  separately as `closure_connectivity_status` and `closure_ack_status`.
  `closure-requirements.tsv` maps each required closure item to its step and
  status; `optional_teardown` is recorded but not required for completion.
  `closure-verdict.env` is the machine gate: `closure_ready=1` only when every
  required closure item is `ok`, and `required_items_failed` lists each missing
  or failed item otherwise.
  Use `scripts/check-wireguard-over-vless-closure-evidence.sh <evidence-dir>`
  to assert the evidence directory is a completed closure; it checks both
  `closure-verdict.env` and `summary.env` for a completed state, requires
  `summary.env` `status=0`, requires the verdict `requirements_file` to point
  at the same evidence directory, and rejects any non-empty
  `required_items_failed` value. It also verifies that each required item in
  `closure-requirements.tsv` matches the last status for that step in
  `steps.log`.
  `steps.log` records each completed, skipped, or failed closure phase so a
  failed run can be resumed from the right boundary.
  `rerun.env` contains only non-secret exports and comments for the next run,
  and can be sourced after adding the missing secret values separately.
- `OVERLAY_CAPTURE_LOG=0`: disable teeing stdout/stderr to `run.log`
- `OVERLAYCTL_BIN`: use a prebuilt `overlayctl`; sudo runtime commands will use
  the same binary and preserve `HOME` so they read the same local state file
- `OVERLAY_BUILD_BIN=1`: build `overlayctl` from `ACCOUNTS_REPO` into the
  evidence directory before checks and use that binary for every CLI step
- `OVERLAY_BUILD_BIN_PATH`: override the build output path; defaults to
  `<OVERLAY_EVIDENCE_DIR>/overlayctl`
- `OVERLAY_ENV_FILES`: comma-separated `.env` files to load before checks. The
  script only imports the closure credential keys it understands and records
  loaded key names, not values, in `summary.env`.
- `OVERLAY_CHECK_ONLY=1`: check local tools plus required environment and write
  evidence without logging in, deploying, or starting the local runtime
- `OVERLAY_USE_SUDO=0`: run `up/status/down` without sudo when the local runtime
  is already permissioned
- `OVERLAY_SKIP_LOCAL_TOOL_CHECK=1`: skip the script's early `python3`, `go` or
  `OVERLAYCTL_BIN`, `ansible-playbook`, `wg`, `wg-quick`, `xray`, and `sudo`
  checks; `overlayctl preflight` still validates runtime tools later
- `OVERLAY_ANSIBLE_SYNTAX_ARGS`: extra args for the syntax-check invocation
- `OVERLAY_ANSIBLE_DEPLOY_ARGS`: extra args for the deploy invocation; defaults
  to `-f 1`, and can be set to values such as `--check --diff`, `--limit host`,
  or `--tags xworkmate_bridge`
- `OVERLAY_SKIP_DEPLOY=1`: skip playbooks deploy when gateways are already deployed
- `OVERLAY_SKIP_UP=1`: stop after render/preflight without starting local runtime
- `OVERLAY_TEARDOWN=1`: run `overlayctl down` after connectivity and ack
- `OVERLAY_TEARDOWN_ON_ERROR=1`: if a command fails after `overlayctl up`
  succeeds, run `overlayctl down` before exiting

## Verification

On both hosts:

```bash
systemctl is-active xray-wg-tproxy wg-quick@wg-xwm xworkmate-bridge-vpn-forwarder xworkmate-bridge
xray run -test -config /usr/local/etc/xray/wireguard-over-vless.json
wg show wg-xwm
```

The role checks `wg show wg-xwm` after service start. It asserts that the
inter-node peer public key and `/32` route are present, that each client attached
to the current node appears as a peer with its `/32` route, and that clients
attached only to the opposite node are routed through the inter-node peer.

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
