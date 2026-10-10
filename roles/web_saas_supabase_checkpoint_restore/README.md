# Supabase SQL checkpoint isolated restore

This role is a UAT-only adapter for the historical checkpoint format produced
by `create_release_checkpoint.sh`:

`supabase-public-sql-gzip-aes256-cbc-v1` = plain PostgreSQL `pg_dump` SQL,
gzip-compressed, then AES-256-CBC encrypted with PBKDF2 (100000 iterations).

It is deliberately separate from the Selfhost `account.dump.enc` custom-format
restore role. Renaming or copying an archive does not make the formats
compatible.

The adapter requires a staged mode-0600 archive beneath the UAT `/data` backup
root and an immutable SHA-256. It validates decryption, gzip, plain-SQL
structure, and rejects destructive/cross-database statements before importing.
It creates exactly one new `release_verify_supabase_<run-id>` database on the
canonical `web-saas-uat` target, refuses an existing or non-empty target, and
does not delete the target automatically.

The historical checkpoint has no source PostgreSQL identity or row/sequence
fingerprints. The receipt therefore reports archive recovery and restored
schema/queryability as passed, while explicitly setting source identity and
row-fidelity binding to unavailable. It cannot authorize migration, login,
subscription, G3, or release acceptance.
