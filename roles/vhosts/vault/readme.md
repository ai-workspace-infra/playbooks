# Vault server role

The default remains the legacy single-node PostgreSQL backend for existing
callers. New single-node servers can use `deploy_vault_single_raft.yml` and a
`vault_single_node` inventory group; the shared three-node entrypoint remains
`deploy_vault_shared_services.yml`. Both use this role and Integrated Storage
(Raft), without automatically initializing or unsealing Vault.

For a multi-node Vault cluster, opt in explicitly to Integrated Storage:

```yaml
vault_deploy_mode: native
vault_storage_backend: raft
vault_ha_enabled: true
vault_raft_expected_members: 3
vault_version: 1.21.4
vault_listen_addr: 0.0.0.0:8200
vault_raft_node_id: vault-prod-0
vault_raft_api_addr: https://vault.svc.plus
vault_raft_cluster_addr: https://10.81.0.10:8201
vault_raft_listener_cluster_addr: 10.81.0.10:8201
vault_raft_retry_join:
  - leader_api_addr: http://10.81.0.11:8200
  - leader_api_addr: http://10.81.0.12:8200
```

Set a unique `vault_raft_node_id`, `vault_raft_cluster_addr`, and listener
address for each host. `vault_raft_retry_join` should contain the other
cluster members' reachable API addresses. These addresses must be private
network addresses; never advertise the public listener as the Raft cluster
address. If API TLS is enabled, use `https://` peer addresses and supply the
appropriate `leader_tls_servername`/CA settings.

Before applying the role:

1. Provide private connectivity between all members for TCP 8200 (Raft join
   and forwarding) and TCP 8201 (Vault cluster traffic). Restrict those ports
   to the cluster network at the cloud firewall and host firewall. Do not
   expose them to the public Internet.
2. Ensure `vault_listen_addr` is reachable by peer members, while the public
   Caddy listener remains the only Internet-facing Vault API endpoint.
3. Configure a shared `api_addr`/front door that clients can reach. This role
   does not manage Caddy, DNS, cloud firewall rules, or TLS certificates.
4. Apply the role to the initial node first. Initialize that cluster exactly
   once with the operator's approved shares/threshold. After the first node is
   initialized and manually unsealed, start the joining nodes; each node must
   be manually unsealed as required by the configured seal method.

For this Raft mode, the role **does not** run `vault operator init`, read or
write `vault_init.json`, execute `vault operator unseal`, create root-token
aliases, enable auth/secrets engines, or bootstrap a Vault admin user. Manage
initialization, unseal, and recovery through the separately controlled
operator procedure. In particular, never put root tokens or unseal shares in
GitHub Actions variables/secrets or Ansible inventory.

The existing PostgreSQL path remains opt-out compatible for single-node
installations. A new single-node Raft installation sets
`vault_storage_backend: raft`, `vault_ha_enabled: false`,
`vault_raft_expected_members: 1`, and an empty `vault_raft_retry_join`.
Three-node HA sets `vault_ha_enabled: true` and
`vault_raft_expected_members: 3`. Both require no swap, a stable private
cluster address and manual init/unseal. Invalid combinations fail before
Vault configuration is changed.

Raft reruns do not restart an already active Vault process. If the role
changes an active node's binary, config or systemd unit, it writes
`/etc/vault.d/.restart-required` and fails. The operator must restart only
that node during a maintenance window, manually unseal it, verify its peer
health, and remove the marker before another deploy.

If the legacy PostgreSQL storage is initialized but `vault_init.json` is
missing, the role now **fails closed**. It never truncates the database to
regenerate keys. Recover the original shares using the operator procedure;
do not rerun installation hoping to repair it.

For an operator-only MFA userpass setup after initialization, see
`files/init_vault_admin.sh`. Supply `VAULT_TOKEN` and
`VAULT_ADMIN_PASSWORD` through a protected local environment, not command
arguments. The script writes a QR PNG and otpauth URI into a mode-0700
directory. Re-running an already enforced account does not destroy or
regenerate its TOTP secret.

For Raft export/import, legacy PostgreSQL-to-Raft migration, backup checks,
and explicit cutover/rollback gates, see `docs/vault/operator-runbook.md`.
