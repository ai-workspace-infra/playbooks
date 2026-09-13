# XConnect One on existing VPS nodes

This role joins an explicitly selected Linux VPS to one declared XConnect Zero
network. It is for fixed service nodes and temporary pipeline nodes; it does
not configure a Gateway and never changes host firewall, SSH, hostname or
default routing.

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

For a disposable or private UAT Gateway using a lab CA, set
`xconnect_one_ca_certificate_source` to the controller-local PEM file. The role
installs it into the system trust store before `join`/`sync`; do not put private
keys or long-lived credentials in this variable.
