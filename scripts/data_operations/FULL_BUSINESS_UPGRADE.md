# Full Accounts business initialization and incremental upgrades

This contract covers all Accounts business domains: users/identities, subscriptions,
quota/policy snapshots, billing/usage ledgers, tenant/RBAC, task and overlay data.
`full_business_contract.py` defines the reviewed 44 legacy tables plus known
incremental additions. An unknown business table or missing legacy table blocks
initialization. Supabase provider-managed auth/storage metadata and environment
migration/checkpoint ledgers are independent and are never replayed.

## Credential prerequisite

`bootstrap_full_business_credentials.py` is an explicit credential metadata
bootstrap. Run with `CONFIRM_FULL_BUSINESS_CREDENTIAL_BOOTSTRAP=true`, canonical
Vault runtime access and `VAULT_ADDR`. It creates a dedicated `readonly_release`
login in each distinct Supabase project, SELECT grants and table-scoped RLS SELECT
policies. NOSUPERUSER, NOINHERIT, NOBYPASSRLS, no memberships, no public-table write
privileges, readonly login default and complete row visibility are verified.
Administrator identities are never snapshot exporter identities.

`kv/uat/database-upgrade` contains backend-specific credentials and a backup
passphrase. Generated credentials are persisted with compare-and-swap before
creating roles. Interrupted runs reuse and authenticate stored credentials. An
unmanaged preexisting role or changed target identity stops the operation; no
implicit password rotation occurs. Unknown existing Vault fields are preserved.
`BOOTSTRAP_STATE=ready` proves both logins were verified, not that data was copied.

## Initialization and release gates

The ordered initialization is PROD Supabase -> Serverless UAT Supabase ->
Selfhost UAT PostgreSQL. Both targets require encrypted backups on a retained
mounted `/data` volume and isolated restoration evidence before data writes.
Full snapshots must use consistent read-only transactions, preserve FK/identity
relationships and prove every reviewed table's convergence. Live session/login
credentials require an explicit UAT identity isolation contract before import.
Source-only legacy structures are adopted through reviewed additive migrations,
not schema replacement. No target row deletion or `replace_public` is implicit.

Initialization is distinct from routine releases: subsequent releases apply exact
version/checksum-bounded incremental SQL and never re-copy PROD data. UAT release
qualification additionally requires nonempty subscription/quota/ledger samples,
real business checks, application rollback preserving upgraded schema/data, and
re-upgrade to the same immutable artifact. Only that evidence can qualify a PROD
Full promotion. This prerequisite implementation does not claim that acceptance.
