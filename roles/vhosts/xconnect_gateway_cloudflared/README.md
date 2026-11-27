# Opt-in Cloudflare Tunnel (`cloudflared`) for XConnect Gateway

This role installs and manages `cloudflared` (`cloudflared-xconnect.service`) on
`jp-xconnect-gateway-01` (`10.67.0.1` on WireGuard `xconzero0`) so Cloudflare
Workers VPC and Hyperdrive can reach private services inside the XConnect
overlay (such as `prod-k3s-v6` PostgreSQL on `10.67.0.2:5432`) without exposing
them to the public internet.

## Single Responsibility

- Installs `/usr/local/bin/cloudflared` (from a reviewed local artifact or a
  pinned upstream release).
- Fetches the Cloudflare Tunnel token at runtime from Vault KV v2 (never stored
  in GitOps) and writes it to `/etc/xconnect-gateway/cloudflared-xconnect.env`
  with mode `0600` (`no_log: true`).
- Installs and enables `cloudflared-xconnect.service`.
- Enforces the anti-deadlock preflight assertion before making host changes.

It does **not** create Cloudflare Tunnels, Workers VPC services, Hyperdrive
configurations, DNS records, or WireGuard topologies.

## Target Host & Preconditions

- Target host: `jp-xconnect-gateway-01` (`10.67.0.1` on `xconzero0`).
- `vhosts/xconnect_gateway` must already be enrolled with `xconzero0` active
  so `10.67.0.2` is reachable across the WireGuard overlay.
- **Anti-deadlock precondition**: `xconnect_gateway_control_plane_url` (and
  `xconnect_gateway_controller` when set) MUST point directly to the Go origin
  (`https://accounts-selfhost-prod-v6.svc.plus`). The preflight fails fast if
  configured to any Cloudflare Worker route (such as `accounts.svc.plus` or
  `*.workers.dev`) that depends on this tunnel, avoiding a circular bootstrap
  deadlock.

## Key Inputs

| Variable | Default | Purpose |
| --- | --- | --- |
| `xconnect_gateway_cloudflared_enabled` | `false` | Opt-in gate; role is a no-op when `false` |
| `xconnect_gateway_control_plane_url` | `https://accounts-selfhost-prod-v6.svc.plus` | Direct Go origin URL checked by anti-deadlock preflight |
| `xconnect_gateway_cloudflared_vault_mount` | `kv` | Vault KV v2 mount name |
| `xconnect_gateway_cloudflared_vault_path` | `""` | Vault KV v2 path containing the tunnel token |
| `xconnect_gateway_cloudflared_vault_token_field` | `tunnel_token` | Field name inside the Vault KV v2 secret |
| `xconnect_gateway_cloudflared_tunnel_token` | `""` | Optional runtime-injected token when already read from Vault |
| `xconnect_gateway_cloudflared_env_file` | `/etc/xconnect-gateway/cloudflared-xconnect.env` | Mode `0600` env file holding `TUNNEL_TOKEN` |
| `xconnect_gateway_cloudflared_service_name` | `cloudflared-xconnect` | Systemd unit name |

## Cloudflare Workers VPC & Hyperdrive Bootstrap Commands

Run these out-of-band operator commands with `wrangler` after the tunnel
(`cloudflared-xconnect.service`) is connected and `prod-k3s-v6` PostgreSQL
forwarding (`10.67.0.2:5432 -> 10.55.35.251:5432`) is active:

1. **Create the Workers VPC TCP service for PostgreSQL (`10.67.0.2:5432`)**:

```bash
npx wrangler vpc service create xconnect-prod-postgresql \
  --type tcp \
  --tcp-port 5432 \
  --app-protocol postgresql \
  --tunnel-id <CLOUDFLARE_TUNNEL_ID> \
  --ipv4 10.67.0.2
```

Save the returned `<VPC_SERVICE_ID>`.

2. **Create the Hyperdrive configuration bound to that VPC service**:

```bash
npx wrangler hyperdrive create xconnect-prod-pg \
  --service-id <VPC_SERVICE_ID> \
  --database <POSTGRES_DB> \
  --user <POSTGRES_USER> \
  --password <POSTGRES_PASSWORD> \
  --scheme postgresql
```

## Verification & Acceptance Evidence

```bash
systemctl status cloudflared-xconnect.service
journalctl -u cloudflared-xconnect.service -n 50 --no-pager
curl -fsS http://127.0.0.1:20241/ready
stat -c '%a %U:%G' /etc/xconnect-gateway/cloudflared-xconnect.env
```

Expected:
- `cloudflared-xconnect.service` is `active (running)` with registered tunnel
  connections.
- `/etc/xconnect-gateway/cloudflared-xconnect.env` has permissions `600 root:root`.

## Idempotent Retry & Rollback

Re-running the role is idempotent; binary download is skipped when the target
version is already present, and systemd restarts only when the env file or unit
changes. To roll back, disable and stop `cloudflared-xconnect.service`, remove
`/etc/xconnect-gateway/cloudflared-xconnect.env`, and set
`xconnect_gateway_cloudflared_enabled: false`.
