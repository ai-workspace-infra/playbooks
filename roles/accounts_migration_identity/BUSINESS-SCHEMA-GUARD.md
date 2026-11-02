# Business schema transaction guard (preparation only)

`files/business_schema_guard.py` builds a transaction-local SQL guard from an
independently verified restore's complete public-table row counts and SHA-256
fingerprints. It binds an independently identified PostgreSQL system identifier
and database OID, locks the reviewed tables, and checks the entire public table
set and each fingerprint before any following schema SQL executes. It normalizes
time zone, date style and floating-point formatting. The caller must begin a
transaction before the guard and retain its locks through the schema operation.
The guard adds short lock and statement timeouts; locks can temporarily block
normal writers, which must be accounted for operationally.

`compose_transaction` verifies a caller-supplied artifact SHA-256, binds the
same identity in transaction-local settings, inserts the guard after the SQL's
single top-level `BEGIN`, and leaves its final `COMMIT` or changes it to
`ROLLBACK` for a dry run. The caller is responsible for obtaining the reviewed
hash from a trusted source and running PostgreSQL with `ON_ERROR_STOP=1`; this
function does not execute SQL or prove that a caller-selected hash is reviewed.

This is a SQL generator, not a production executor or backup receipt. It cannot
verify that a restored database came from the reviewed encrypted archive, that
the restore remained unchanged, or that schema, sequences, extensions, roles,
ACLs and object ownership match. It does not pin the SQL artifact, collect
fingerprints, issue a receipt, perform recovery, or establish a migration API
backup gate. A separate reviewed owner operation must establish those facts,
consume the generator in the same transaction as the pinned schema SQL, and
record outcome before any production execution. Do not supply target data as a
substitute for a verified independent restore.

Regression: `ACCOUNTS_IDENTITY_TEST_DSN=... python -m unittest
tests/test_accounts_business_schema_guard.py -v` requires an isolated loopback
PostgreSQL instance. It creates and removes only its own randomly named fixture
database and checks identity, table-scope and data drift rejection, lock
contention, and transactional rollback.
