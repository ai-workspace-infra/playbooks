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
field unless `overwrite: true` (JSON boolean only; `"false"`/`1` are rejected,
as for `apply`). KV paths must start with the selected environment prefix
(`sit/`, `uat/`, `prod/`); cross-environment writes are rejected before any Vault
request. There is no caller bypass flag (`allow_shared_path` is rejected); a
legacy shared layout needs an exact `(environment, mount, path)` entry in the
reviewed `REVIEWED_SHARED_KV_PATHS` list (empty today); policy capabilities limited to
`allowed_capabilities` (default read); field values, tokens and CA private
material never printed. Cloud IAM/provider operations are not handled here
(IaC). Declarations come from GitOps `security/vault/`; the caller passes them.

For `jwt_contract`, the caller may pass a `contract` containing
`role_declaration` and `policy_declaration`: the parsed GitOps
`security.gitops/v1alpha1` `VaultJWTAuthRole` and `VaultACLPolicy` mappings.
The executor checks kind, API version, matching names, unique policy paths and
supported fields before translating them to the Vault API format. A contract
must bind `ref` and an exact workflow path via `workflow_ref` or
`job_workflow_ref`; wildcard path components are rejected. The role must name
only its own policy. Ref patterns remain governed by the reviewed declaration.
The caller must select a clean GitOps checkout at the run's recorded commit and
parse the YAML before passing it; these mappings alone do not prove provenance.
Token TTL values are compared with Vault's returned seconds, including explicit
maximum TTL, so a changed TTL is reconciled and an equivalent duration does not
cause a redundant write. Runtime auth mounts must be single validated names.

Host-side SSH CA trust (`vault/install_legacy_ssh_ca.sh`) lives in
`roles/vhosts/vault_ssh_ca_trust` (`vault_ssh_ca_trust.yml`). That role first
does read-only conflict checks (existing CA, drop-in, principals, sudoers), then
validates a candidate tree with `sshd -t` and `sshd -T` (drop-in must be
effective), and only then installs; a CA or drop-in change notifies a reload
that re-checks the live config first. A different existing CA is rejected unless
the rotation contract is given (`vault_ssh_ca_trust_rotate_from_fingerprint` +
`ROTATE-VAULT-SSH-CA-<host>`). No sudo is granted by default; NOPASSWD ALL needs
`vault_ssh_ca_trust_management_purpose: vault-legacy-migration` plus
`GRANT-NOPASSWD-ALL-<principal>`. Isolated tests: `tests/vault_ssh_ca_trust/`.
