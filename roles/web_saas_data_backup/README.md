# Web SaaS encrypted data backup

Creates one immutable encrypted custom-format archive beneath
`/data/backups/web-saas/<environment>/<release-tag>/<run-id>/`. The separate
`web_saas_data_restore_verify` role restores it into `release_verify_<run-id>`.
The actual `/data` mount is mandatory. The script streams `pg_dump` directly
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

The generated evidence separates `restore_gate_status=passed` from
`g3_status=blocked` when subscriptions are zero. The database-component
evidence does not prove G1/G2/G3, actual user login, permissions, entitlement
semantics, or promotion readiness. No rollback is needed for the primary database; retain the immutable
encrypted archive according to the caller's approved retention policy.
