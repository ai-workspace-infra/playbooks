# Web SaaS isolated restore verification

Independently decrypts the immutable `/data` archive into the unique
`release_verify_<run-id>` database on the same PostgreSQL instance. It refuses
an existing database of that name, rechecks source database identity metadata,
exact clean migration version, nonempty user/subscription counts, and schema
fingerprint and full-row/sequence fingerprint, then compares those values with the restored copy. Cleanup drops
only the temporary database created by this process and only while its database
OID still matches the recorded OID. The `account` database is never a restore
target.

The run-owned verification database is created from template0; only its empty
default public schema is removed (without CASCADE) so the archive can create
the schema. No existing or primary schema is dropped or rewritten.

Required inputs are the same source ID, frozen baseline ID, authorized sample
reference, expected schema version, expected sample counts and schema hash,
run/environment-bound archive path and checksum, PostgreSQL system identifier,
container, and runtime passphrase as the backup role. It compares the source
and restored PostgreSQL system identity, public schema and full data fingerprints. The role
emits component evidence only; it cannot establish G1/G2/G3 business acceptance
or production promotion.

The caller owns exact CMDB host selection. Primary database data is not changed, and
there is no rollback action beyond exact cleanup of a run-owned temporary
database.
