# Vault server operator runbook

This runbook is for an interactive, trusted operator terminal. GitHub Actions
may provision nodes, install Vault, monitoring and XConnect, and verify
non-sensitive health. It **must not** receive a Vault root token, unseal
shares, MFA QR, PostgreSQL password, or backup image.

## Deployment gates

1. Deploy a new single-node Raft server with `deploy_vault_single_raft.yml`, or
   three nodes with `deploy_vault_shared_services.yml` and the separate
   `vault-shared-leader` and `vault-shared-peers` tags. Bind Raft peer traffic
   to private 8200/8201 addresses, keep public 443 on Caddy, and allow public
   SSH only during bootstrap. Verify fixed SSH host keys before each stage.
2. Manually initialize only the **new, empty** leader. Distribute unseal
   shares to separate custodians over an out-of-band channel. Do not write
   `vault operator init -format=json` output to this repository, Actions,
   GitHub Secrets/Variables, or the shared Vault cluster being migrated.
   Manually unseal the leader. If migrating existing PostgreSQL storage,
   **do not initialize the Raft destination**; use the offline migration
   procedure below and the original seal shares instead.
3. Install peers, manually unseal each, and confirm one leader, two healthy
   peers and Raft quorum using `vault operator raft list-peers` and
   `vault operator raft autopilot state`. Keep this a human approval gate;
   the automation cannot advance from an unsealed but unhealthy cluster.
4. Install node exporter, process exporter and Vector through the shared
   services playbook. Configure `xconnect-gateway` on node 0 and
   `xconnect-one` on nodes 1/2 using short-lived Zero invitations supplied
   outside Git. The operator Mac joins with its own one-time invitation.
   Verify the signed network, overlay IPs, internal DNS and Mac-to-SSH-only
   policy. Only then switch GitOps SSH mode to `xconnect-zero`, verify an
   overlay-only deployment, and remove the public SSH allowlist.

## MFA administration

On the trusted operator host after Vault is unsealed, obtain a short-lived
administrator credential without putting it on a command line or in shell
history. Read `VAULT_TOKEN` and `VAULT_ADMIN_PASSWORD` into the protected
operator environment, then run:

```bash
VAULT_ADDR=http://127.0.0.1:8200 \
  roles/vhosts/vault/files/init_vault_admin.sh \
  --username admin --output-dir "$HOME/.local/share/vault-admin-enrollment"
```

The script creates a private PNG QR and otpauth URI. Scan the QR locally,
test a userpass login with TOTP, then securely remove the enrollment files.
The QR/URI is a second-factor seed: never commit, upload, or print it in CI.
Do not rerun admin generation to rotate MFA; an existing enforcement is left
untouched. Any recovery or rotation needs a separate operator procedure.

## Raft snapshot export and import

On an authenticated operator terminal with `VAULT_ADDR` and `VAULT_TOKEN`, use
`roles/vhosts/vault/files/vault_operator_data.sh`:

```bash
vault_operator_data.sh snapshot-save --output /secure/offsite/vault-2026-09-25.snap
vault_operator_data.sh snapshot-inspect --input /secure/offsite/vault-2026-09-25.snap
```

`snapshot-save` refuses to overwrite, verifies the snapshot, and produces a
mode-0600 file. Store a copy off the Vault nodes with restricted access. A
restore overwrites destination data and needs explicit confirmation of the
destination cluster ID:

```bash
vault status -format=json | jq -r '.cluster_id'
vault_operator_data.sh snapshot-restore \
  --input /secure/offsite/vault-2026-09-25.snap \
  --confirm-cluster-id '<destination-cluster-id>'
```

Restore returns before background replay completes. Keep traffic closed
until server logs, seal state, cluster peers, auth mounts and a representative
KV read have been checked. A Raft snapshot cannot be taken directly from the
legacy PostgreSQL Vault.

## Offline PostgreSQL-to-Raft migration

The old PostgreSQL-backed Vault cannot become a Raft peer or be rolled into
the new cluster. Schedule a write freeze and maintenance window. Take a
separate PostgreSQL backup (`pg_dump` with credentials supplied through a
protected environment or passfile) and verify it off-node. Record the old
Vault seal configuration and custody of the original shares. Stop the old
Vault service and confirm the old health endpoint is unavailable.

Use a private, mode-0600 HCL configuration on the migration host. Supply the
actual PostgreSQL connection URL through this local file; **never** commit
it to GitOps or put its password in command arguments. The Raft path must be
empty and must match the new leader's storage path/node ID:

```hcl
storage_source "postgresql" {
  connection_url = "postgres://<operator-supplied-connection-url>"
}
storage_destination "raft" {
  path = "/opt/vault/data"
  node_id = "vault-prod-0"
}
cluster_addr = "https://<private-leader-ip>:8201"
```

```bash
vault_operator_data.sh migrate-offline \
  --config /secure/migrate.hcl \
  --destination-path /opt/vault/data \
  --source-health-url https://<old-vault-private-host>:8200/v1/sys/health \
  --confirm MIGRATE-POSTGRESQL-TO-RAFT
```

The script checks that the source no longer answers, the HCL is private and
the destination is empty; the operator must independently verify a clean
backup, stopped writers and the HCL's actual path. Vault's offline migrate
copies storage without decrypting, **overwrites destination data**, and
preserves the original seal; do not run `vault operator init` on the result.
Start the migrated leader, manually unseal it with the original shares, and
verify a representative subset of secrets/auth before installing peers.

Keep `vault.svc.plus` pointed at the old node during the entire validation.
Cut over Caddy/DNS only after Raft quorum, access policies, monitoring and
XConnect connectivity pass. Preserve the old node and PostgreSQL backup for
rollback, and do not restart old and new writers against the same data.
If verification fails, close new traffic, restore the old routing and resume
the old Vault only after confirming no concurrent writes. Rotate any root
token or seal share that has been exposed outside its custody channel.

References: [HashiCorp storage migration](https://developer.hashicorp.com/vault/docs/commands/operator/migrate),
[Raft snapshots](https://developer.hashicorp.com/vault/docs/commands/operator/raft),
[login MFA](https://developer.hashicorp.com/vault/docs/auth/login-mfa).
