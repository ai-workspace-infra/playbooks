# Web SaaS isolated restore verification

Explicit full-database restores retain object owners/ACLs/default grants and
compare the ownership/ACL-inclusive schema fingerprint, not just table shapes.
All referenced roles must already exist; no ad-hoc post-restore grant repair or
role-password substitution. The isolated Accounts API fixture additionally
verifies real account_user TCP SELECT/INSERT rollback transactions before proof
or apply. Global role/credential provisioning remains separate.

Independently decrypts the immutable `/data` archive into the unique
`release_verify_<run-id>` database on the same PostgreSQL instance. It refuses
an existing database of that name, rechecks source database identity metadata,
exact clean migration version, nonempty users and the exact subscription count
(including zero), and schema
fingerprint and full-row/sequence fingerprint, then compares those values with the restored copy. Cleanup drops
only the temporary database created by this process and only while its database
OID still matches the recorded OID. The `account` database is never a restore
target.

The run-owned verification database is created from template0. In the default
public-only mode its empty default public schema is removed (without CASCADE)
so the archive can create it. With `web_saas_data_restore_verify_full_database: true`,
the default schema is retained, matching full `pg_dump` semantics.
No existing or primary schema is dropped or rewritten.

Required inputs are the same source ID, frozen baseline ID, authorized sample
reference, expected schema version, expected sample counts and schema hash,
run/environment-bound archive path and checksum, PostgreSQL system identifier,
container, and runtime passphrase as the backup role. It compares the source
and restored PostgreSQL system identity, public schema and full data fingerprints. The role
emits `restore_gate_status=passed` independently; zero subscriptions are
recorded as `g3_status=blocked`. It cannot establish G1/G2/G3 business acceptance
or production promotion.

The caller owns exact CMDB host selection. Primary database data is not changed, and
there is no rollback action beyond exact cleanup of a run-owned temporary
database.

`web_saas_data_restore_verify_allow_empty_users` explicitly permits a zero-user
target; old callers still require populated users by default. Empty-target
mechanism checks do not prove populated-data acceptance. Full-database mode
compares all schema definitions plus complete public rows and sequence states.
Explicit `sit` is supported without changing the supplied environment label.
