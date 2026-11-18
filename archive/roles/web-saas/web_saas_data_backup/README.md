# Web SaaS encrypted data backup

The explicit full-database path preserves object ownership, ACLs and default
grants in both the custom archive and schema fingerprint. Its evidence reports
`privileges_preserved: true`; the API gate refuses older stripped-ACL proofs.
Global login-role credentials remain outside the archive and under their Vault
owner. Legacy public-only defaults retain their existing behavior.

Creates one immutable encrypted custom-format archive beneath
`/data/backups/web-saas/<environment>/<release-tag>/<run-id>/`. The separate
`web_saas_data_restore_verify` role restores it into `release_verify_<run-id>`.
This is an online backup: the source application, Doco-CD and PostgreSQL remain
running. The script holds a PostgreSQL exported MVCC snapshot across row/schema
fingerprinting and `pg_dump`, so the archive represents one consistent database
boundary. Writes committed after that boundary are not included; reconcile them
later on the isolated target when required. Snapshot creation itself never
requires freezing source writers.
The default dedicated profile requires an actual `/data` mount. The explicit
`web_saas_data_backup_storage_profile: root` additionally permits `/data` on the
bundled root filesystem for a verified candidate. The script streams `pg_dump` directly
through OpenSSL; no plaintext dump or password file is created. The password is
received only as a task environment value and is hidden from Ansible output.

Required inputs: environment, release tag, numeric run ID, exact expected clean
schema version, source database identifier, frozen baseline identifier,
authorized subscription sample reference, and a 32-character-or-longer
encryption passphrase. The source must contain one clean migration row and
nonempty users; subscriptions may be zero for an isolated recovery rehearsal.
Source and restored migration state, counts,
and public schema fingerprint must match. Full table rows and sequence states
are fingerprinted without emitting data. This role cleans only its own pending
archive; the restore role owns temporary-database cleanup with an OID check.
Neither drops `account`, follows a reused test
database, or rebuilds storage.

New API-owner opt-ins preserve old defaults: `web_saas_data_backup_full_database`
includes all database schemas in the encrypted archive and schema fingerprint;
`web_saas_data_backup_allow_empty_users` permits an honest zero-user recovery
drill. Full public row/sequence equality remains mandatory. Empty-target evidence
does not establish a populated recovery drill or business acceptance. Supported
environment labels also include explicit `sit` without a UAT remapping.

The generated evidence separates `restore_gate_status=passed` from
`g3_status=blocked` when subscriptions are zero. The database-component
evidence does not prove G1/G2/G3, actual user login, permissions, entitlement
semantics, or promotion readiness. No rollback is needed for the primary database; retain the immutable
encrypted archive according to the caller's approved retention policy.
