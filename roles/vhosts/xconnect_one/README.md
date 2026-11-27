# XConnect One on existing VPS nodes

This role joins an explicitly selected Linux VPS to one declared XConnect Zero
network. It is for fixed service nodes and temporary pipeline nodes; it does
not configure a Gateway and never changes host firewall, SSH, hostname or
default routing.

The default join is intentionally minimal: observability and overlay DNS are
off, foreign overlay release is off, and the role verifies that the exact host
default route is unchanged after the join. Enable those features explicitly
per node only after the overlay handshake and private connectivity pass.

`xconnect_one_environment` accepts `sit`, `uat`, `prod`, or `shared`. Use `shared`
for shared-service nodes such as the Vault cluster; this only namespaces local
state/configuration paths and telemetry labels, and does not reuse UAT/PROD
network credentials.

The controller must provide a reviewed CLI artifact and a short-lived join URI
at runtime. The URI is written only to a mode `0600` transient file and is
removed after the exchange. GitOps contains node identity, environment and
non-sensitive network contracts only.

The collision preflight is repeat-safe for an existing state file that matches
the declared network, device, CIDR and interface. It still rejects an
interface, overlay route, or local transport port owned by another network or
an inconsistent state file.

Before any runtime installation, the role refuses an overlay CIDR that overlaps
an existing address or route, an occupied WireGuard interface, or the Xray
loopback UDP port. A fixed One uses `persistent` lifecycle; a Spot One must use
`ephemeral` lifecycle with an Accounts lease. At expiry Accounts revokes the
device, increments the network generation, and Gateway removes the peer on its
next signed-config sync.

Set `xconnect_one_ca_certificate_source` to the controller-local PEM file
fetched from the selected Gateway's `ca.crt` handoff. The role installs the
public CA into the system trust store before `join`/`sync`; it never reads Vault,
creates a CA, or accepts a Gateway private key. For the standard UAT Gateway,
the source ultimately comes from the domain certificate record
`kv/data/CICD/domains/svc.plus`, not from a runner-generated certificate.

Set `xconnect_one_dns_enabled: true` with `xconnect_one_dns_server` pointing
to the Gateway overlay address and `xconnect_one_dns_domains` containing the
route-only private suffixes (for example `shared.internal` and `svc.plus`).
The role uses `systemd-resolved` on the WireGuard interface, so public names
continue to use the host's normal resolver while the declared private suffixes
go to the Gateway DNS forwarder. It points `/etc/resolv.conf` at the
`systemd-resolved` stub by default; set `xconnect_one_dns_manage_resolv_conf`
to `false` when the host owns resolver configuration elsewhere.

## Vault KV invitation handoff

Zero issues a short-lived, single-use, device-bound invitation. The enrollment
orchestrator writes its complete join URI to the KV v2 field `join_uri`.
For each device set `xconnect_one_invite_vault_path` to
`<environment>/services/xconnect/one-invites/<device-id>` and, if needed,
`xconnect_one_invite_vault_mount` to the KV v2 mount name (default `kv`).
The selected environment and device ID must match the path. An already joined
device does not read Vault or consume another invitation.

The controller executes the Vault read using the configured Vault connection and
short-lived identity. Grant it read access to the exact device record only.
The CLI artifact must support `join --invite-file`. The role validates the field,
stages it on the target in a protected transient
file, and removes that file after the join attempt. The issuer owns expiry,
revocation, replacement, and cleanup of the KV record. A consumed or expired
invitation needs a new Zero-issued invitation. Vault KV storage does not itself
make the invitation single-use or revoke it.

The Vault writer, OIDC role, ACL, record retention and cleanup workflow must
be provisioned and verified separately before using this option. This role
does not issue invitations or write to Vault. Existing invite URI and file
inputs remain available, and must not be combined with a Vault path.

## Opt-in Private PostgreSQL TCP Forwarding

For service nodes that expose an internal PostgreSQL instance over the XConnect
overlay to a designated Gateway (for example `prod-k3s-v6` exposing
`10.67.0.2:5432 -> 10.55.35.251:5432` to `jp-xconnect-gateway-01` at
`10.67.0.1`), set `xconnect_one_pg_forward_enabled: true`.

- `xconnect_one_pg_forward_bind_address` (`10.67.0.2`) MUST be a specific
  WireGuard IPv4 address on `xconnect_one_pg_forward_interface` (`xconzero0`),
  never `0.0.0.0`.
- `xconnect_one_pg_forward_allowed_source_cidr` (`10.67.0.1/32`) enforces a
  strict `/32` peer restriction in both `nftables` (`inet xconnect_one_pg_forward`)
  and `socat` (`range=10.67.0.1/32`).
- Run standalone via `deploy_xconnect_one.yml --tags xconnect-one-pg-forward -e xconnect_one_pg_forward_enabled=true`.
