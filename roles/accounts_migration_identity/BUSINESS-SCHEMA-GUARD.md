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

An independently captured target catalog SHA-256 is also checked after the
table locks: it covers public table and column definitions, constraints,
indexes, triggers, row policies, function definitions, ownership and ACL fields,
sequence definitions, and installed extension versions. This detects drift from
that target observation; it does not establish equivalence to the restore or
cover every PostgreSQL catalog object, role membership, sequence value or ACL
semantics. The table locks do not serialize all possible concurrent function,
role or extension DDL, so administrators must coordinate schema changes.

`compose_transaction` verifies a caller-supplied artifact SHA-256, binds the
same identity in transaction-local settings, inserts the guard after the SQL's
single top-level `BEGIN`, and leaves its final `COMMIT` or changes it to
`ROLLBACK` for a dry run. The caller is responsible for obtaining the reviewed
hash from a trusted source and running PostgreSQL with `ON_ERROR_STOP=1`; this
function does not execute SQL or prove that a caller-selected hash is reviewed.

`files/business_schema_preflight.py` connects to a private local restore socket,
checks the encrypted archive hash and clean pinned Accounts/GitOps revisions,
collects restore table fingerprints in a read-only snapshot, then sends only a
guarded rollback transaction to the declared business target over verified SSH.
It writes a new private receipt only after the rollback succeeds. It has no apply
mode; it cannot authorize or perform a production schema change or data import.

This is a SQL generator and rollback-only preflight, not a production executor or backup receipt. It cannot
verify that a restored database came from the reviewed encrypted archive, that
the restore remained unchanged, or that schema, sequences, extensions, roles,
ACLs and object ownership match the backup. It does not authenticate the caller's
SQL hash, collect fingerprints, issue a receipt, perform recovery, or establish a migration API
backup gate. A separate reviewed owner operation must establish those facts,
consume the generator in the same transaction as the pinned schema SQL, and
record outcome before any production execution. Do not supply target data as a
substitute for a verified independent restore.

Regression: `ACCOUNTS_IDENTITY_TEST_DSN=... python -m unittest
tests/test_accounts_business_schema_guard.py -v` requires an isolated loopback
PostgreSQL instance. It creates and removes only its own randomly named fixture
database and checks identity, table-scope and data drift rejection, lock
contention, and transactional rollback.
