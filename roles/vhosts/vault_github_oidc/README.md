# Vault GitHub OIDC reconciliation

Local operator entrypoint: `reconcile_vault_github_oidc.yml`.
Pass `vault_github_oidc_request` with a clean GitOps checkout path, its exact
40-character commit SHA, one declared `role_name`, and explicit boolean
`apply`. The role reads the selected `VaultJWTAuthRole` and only its referenced
`VaultACLPolicy` declarations from that checkout; it does not accept copied
role or policy maps. `VAULT_ADDR` and a short-lived admin `VAULT_TOKEN` are
read from the execution environment and never enter Ansible arguments,
inventory, artifacts, or committed vars. Default operational guidance is
`apply: false`; inspect the redacted diff/counts before an explicitly approved
`apply: true` run.

Example non-secret request file:

```yaml
vault_github_oidc_request:
  gitops_root: /absolute/path/to/clean/gitops-checkout
  gitops_sha: <resolved-40-character-commit-sha>
  role_name: <declared-vault-jwt-role-name>
  apply: false
```

Set `VAULT_ADDR` and `VAULT_TOKEN` in the authenticated operator environment,
then run `ansible-playbook reconcile_vault_github_oidc.yml -e @/path/to/request.yml`.
The apply run uses the same exact GitOps checkout and SHA with `apply: true`.

The role checks all input before mutation, preserves unselected roles/policies,
and compares existing configuration before writing. It requires the JWT mount,
GitHub issuer, Vault audience, exact repository/workflow/ref bindings, batch
tokens, and no default policy. Production release roles remain limited to
stable `v*` tags or protected `release/v*` branches. A protected production
`main` role is accepted only with an exact workflow/ref, protected `prod`
environment, a 30-minute TTL, and policies containing exact paths with
`read` capability only. Other production `main` roles are rejected.

Vault must reach GitHub discovery/JWKS over verified HTTPS. DNS/network policy
is declared separately in GitOps. Role presence is not proof that every consumer
has completed a real CI login. Record actual job results separately.

Validate with `ansible-playbook reconcile_vault_github_oidc.yml --syntax-check`
and `python3 tests/test_vault_github_oidc_guards.py`. Rerunning the same
declaration SHA should report `changed=0`. To roll back, reconcile the previous
approved GitOps declaration SHA; do not disable the mount or delete unrelated roles.
