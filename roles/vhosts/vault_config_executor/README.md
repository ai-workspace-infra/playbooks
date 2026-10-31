# Vault configuration executor

Owner of Vault *content* changes formerly done by Platform-Orchestrator
`scripts/vault/*` and `scripts/websaas/bootstrap_accounts_runtime_kv.sh`.
Entry: `vault_config_executor.yml` with `VAULT_CONFIG_REQUEST` (JSON) and an
optional `VAULT_CONFIG_RESULT` receipt path.

| operation | replaces (Orchestrator) | writes |
|---|---|---|
| `ssh_ca` | `vault/bootstrap_shared_ssh_ca.sh` | SSH mount, CA (only if absent), signing role |
| `jwt_contract` | `vault/uat_cleanup_auth_contract.sh`, `create_vault_service_repo_roles.sh` (per-role) | one ACL policy + JWT role |
| `kv_fields` | `websaas/bootstrap_accounts_runtime_kv.sh`, `vault/write-prod-ssh-deploy-key.sh`, `vault/update-feature-lane-vault.sh` | selected KV v2 fields with CAS |

Common input: `operation`, `environment` (sit/uat/prod), `address` (HTTPS),
`token` (short-lived, from the caller's OIDC login), `apply` (default false),
`confirm` = `APPLY-VAULT-<OPERATION>-<ENV>` when applying.

Guarantees: preview by default; CA never regenerated; KV writes use CAS on the
read version, PUT only when absent, and never overwrite an existing differing
field unless `overwrite: true`; policy capabilities limited to
`allowed_capabilities` (default read); field values, tokens and CA private
material never printed. Cloud IAM/provider operations are not handled here
(IaC). Declarations come from GitOps `security/vault/`; the caller passes them.

Host-side SSH CA trust (`vault/install_legacy_ssh_ca.sh`) lives in
`roles/vhosts/vault_ssh_ca_trust` (`vault_ssh_ca_trust.yml`).
