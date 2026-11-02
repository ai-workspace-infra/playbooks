# Accounts migration identity owner

Manual entry: `accounts-migration-identity.yml`. Requires Python with PyYAML,
Vault CLI authenticated to the approved Vault, kubectl on the selected node,
and a verified SSH host key. This role consumes a clean, exact approved GitOps
SHA and a GitOps-relative operation path; target SSH identity is supplied by the
operator. No production target or credential is defaulted.

Phases: `secrets` persists new database/staging credentials in the declared Vault
KV path and delivers Kubernetes Secrets; `init` runs the fixed native initializer
only in the new target candidate; `permissions` provisions scoped roles. Existing
source readonly_release credentials are set to the Vault-controlled migration
credential. Review other consumers before running this source phase. Initialization
refuses populated candidates and preserves failed Jobs. Never delete/retry a
failed initialization blindly.

Run secrets after Flux creates the namespace, target init after PostgreSQL starts,
then permissions on both sides. Source/target listener Pods may remain blocked
until these dependencies exist. Secrets never appear in argv or receipts. Private
staging is encrypted by the listener with distinct Vault-owned keys.

Rollback: remove routing and scale listeners to zero through GitOps. Preserve
candidate/staging PVCs and Vault material for evidence; no automatic deletion or
source writer/DNS change is implemented. Apply remains disabled without a separate
verified backup gate. This operation is not an apply or cutover authorization.

Source credential rotation requires `migration_allow_source_rotation=true`.
The source role name is enforced by migratectl; the owner deliberately stops
without explicit approval rather than silently replacing an existing password.
Use `migration_phase=secrets`, then target `init`, then `permissions` on each
side, with `migration_gitops_dir`, `migration_gitops_sha`, `migration_operation`
and `migration_ssh_target` supplied explicitly. Vault CLI authentication is
required locally; no Vault token argument or value is recorded.
