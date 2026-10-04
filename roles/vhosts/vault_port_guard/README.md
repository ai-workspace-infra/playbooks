# Vault port guard

Loads an nftables table that restricts Vault's API and Raft ports
(`vault_port_guard_ports`, default 8200/8201) to loopback and one declared
interface (`vault_port_guard_interface`, the XConnect overlay). Everything
else is dropped, regardless of any other firewall or cloud security group.

Set `vault_port_guard_enabled: true` to install and load the ruleset, or
`false` to remove it. The unit (`vault-port-guard.service`) runs before
`vault.service` on boot so the guard is always in place before Vault's
listener can bind.

Used by `vhosts/vault_legacy_migration`'s `convert` action; can also be
applied directly to any Raft node that should never expose 8200/8201
publicly.
