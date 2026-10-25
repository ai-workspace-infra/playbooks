# Vault GitHub OIDC reconciliation

Local operator entrypoint: `reconcile_vault_github_oidc.yml`.
Pass `vault_github_oidc_request` at runtime: HTTPS `address`, short-lived admin
`token`, selected `roles` and referenced `policies` maps, and explicit `apply`.
Default is preview. Load declarations from a recorded immutable source commit.
Never put the runtime token into inventory, command arguments or committed vars.

The role checks all input before mutation, preserves unselected roles/policies,
and compares existing configuration before writing. It requires the JWT mount,
GitHub issuer, Vault audience, repository/workflow/ref bindings, one-hour batch
tokens, and stable release refs for production. Ordinary SIT/UAT/PROD roles only;
bootstrap, operator and legacy migration identities are deliberately excluded.
Policy capabilities come from the reviewed input and are not expanded by this role.

Vault must reach GitHub discovery/JWKS over verified HTTPS. DNS/network policy
is declared separately in GitOps. Role presence is not proof that every consumer
has completed a real CI login. Record actual job results separately.

Validate with `ansible-playbook reconcile_vault_github_oidc.yml --syntax-check`.
Rerunning the same request should report changed=0. To roll back, reconcile the
previous approved declaration; do not disable the mount or delete unrelated roles.
