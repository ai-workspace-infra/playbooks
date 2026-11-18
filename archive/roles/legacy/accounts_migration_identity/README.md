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

## Existing business target

An explicitly declared `target_mode: business` supports a separate operation
for an existing, unversioned target; `schema_mode` must be
`unversioned_verified_scope`. It requires 42 unique table names, a target
Vault path, a distinct existing `staging_key_vault_path`, and a staged
`listener_secret_name` different from `migration-listener`. The `secrets` phase
creates or reuses the target database password in its Vault path, reads the
existing staging key without rotating it, and delivers only the staged Secret.
It does not change the active listener, its TLS Secret or candidate resources.

The `init` phase is forbidden in business mode. After a separately reviewed
schema operation, `permissions` requires all 42 declared tables to exist and
`schema_migrations` to be absent. It refuses elevated, assumable or table-owning
roles, existing DELETE/TRUNCATE privileges, unsafe non-loopback trust HBA rules,
and incompatible row-security policies. It grants the declared target scope
SELECT/INSERT/UPDATE without DELETE; the API still independently verifies
column/PK/FK and visibility before preview. An existing migration login must
authenticate using the Vault-managed password before grants; its password is
not reset. A newly created login uses that same Vault-managed password.

Authentication uses the declared PostgreSQL Service address: a synthetic wrong
password must fail, the managed password must work, and the observed client
address must be non-loopback. This prevents local PostgreSQL `trust` rules from
being mistaken for credential validation. Credentials flow via stdin, not argv.
An authentication failure after first-time role creation preserves the role and
reports failure; do not assume that the operation rolled back after commit.

This role does not apply target schema SQL, install migration receipt/sequence
helpers, enable unversioned listener mode, issue a backup receipt or switch the
active Secret. Those steps and their validation remain separate. The staged
Secret and existing encrypted data should be retained for controlled retry.
