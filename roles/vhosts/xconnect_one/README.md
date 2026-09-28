# XConnect One on existing VPS nodes

This role joins an explicitly selected Linux VPS to one declared XConnect Zero
network. It is for fixed service nodes and temporary pipeline nodes; it does
not configure a Gateway and never changes host firewall, SSH, hostname or
default routing.

`xconnect_one_environment` accepts `uat`, `prod`, or `shared`. Use `shared`
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
