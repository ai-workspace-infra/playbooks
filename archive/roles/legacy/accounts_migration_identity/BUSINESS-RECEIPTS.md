# Target migration receipts

`files/business_receipt_bootstrap.sql` prepares the exact target-only receipt
tables expected by Accounts `InitializeMigrationReceipts`. The owner must bind
the independently verified PostgreSQL system identifier and database OID in
`accounts_migration_guard.system_identifier` and
`accounts_migration_guard.database_oid` in the same session before execution.
It requires an independently verified current target backup and a reviewed SQL
checksum. Use `psql -X -v ON_ERROR_STOP=1` and retain the operation result.

The script creates `accounts_migration_api` only if it is absent, restricts
public access, and grants the non-owner `accounts_migration` role only schema
USAGE and receipt SELECT/INSERT. It refuses privileged or assumable roles,
business table ownership, and unexpected ACLs, including extra grants inherited
from default privileges. Everything is one transaction. An existing schema
causes a refusal rather than silently reusing an unreviewed object; inspect it
and the first operation's receipt before retrying.

This initializes metadata storage only. It does not enable listener apply,
verify a migration backup receipt, modify business records or install trigger
and sequence helpers. Preview does not require this schema. No live operation
is implied by merging the SQL artifact.

Run the isolated fixture with `ACCOUNTS_IDENTITY_TEST_DSN=... python -m unittest
tests/test_accounts_receipt_bootstrap.py -v`. The fixture creates and removes
only its randomly named database and temporary dedicated test roles on an
explicit loopback PostgreSQL instance.
