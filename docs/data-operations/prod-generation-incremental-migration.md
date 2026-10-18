# Versioned Selfhost PROD generation: additive migration

Use `web-saas-generation-data-operations.yml` only against one exact
`web-saas-prod-vN` inventory target. It is designed for SSH/Ansible operation;
it does not dispatch CI, alter public DNS, access a prior generation, or target
the stable `web-saas-prod` host.

## Required evidence and inputs

The operator supplies an immutable release identity, exact target database ID,
current clean schema version, candidate image digest, Accounts source commit,
the single reviewed SQL checksum, bounded lock/statement timeouts, an explicit
approval record, and baseline provenance for an earlier `prod-vN`. The
baseline manifest is metadata only: it does not copy, initialize, or mutate
database rows. Use a unique numeric run ID and bind it into the migration
request.

Build the migrator for the target host architecture (`linux/amd64` for the
current V3 GCP host) from the exact Accounts source commit. Stage only the
binary and reviewed migration directory; the DSN remains inside the running
Accounts container and is read via `DATABASE_URL`, never passed as a process
argument.

The backup passphrase is supplied only at runtime from an approved local
secret source or Ansible Vault. Never put it in Git, the command line, inventory,
receipts, or logs. The playbook writes an encrypted database archive under
`/data/backups/web-saas/<prod-vN>/<release-tag>/<run-id>/`, with private
directory/file permissions, then restores it into a run-specific temporary
database and compares schema, full table-row/sequence fingerprints, clean
migration version, user/subscription counts, and archive checksum. The
temporary restore database is removed after verification; the encrypted
checkpoint remains on the target host.

For `backup-restore`, set the operation to that literal value; it creates and
verifies a checkpoint but does not migrate. For `migration`, the same invocation
must pass both backup and restore gates before `web_saas_data_migration` runs
the exact reviewed `migratectl migrate` command twice. The operation refuses
dirty state, mismatched checksums/versions/host/container/run IDs, stable PROD,
or a source generation that is not older than the target. A migration receipt
is one component gate; perform post-migration read-only business comparisons
and application acceptance separately.

## V3-specific guardrails

- V2 is a read-only provenance source; this playbook does not connect to it.
- V3 and later use `web-saas-accounts` and `web-saas-postgresql` containers.
- This data operation does not stop or restart the application. If concurrent
  writes change during backup/restore comparison, it fails closed; investigate
  and repeat the complete checkpoint flow with a fresh run ID.
- Never force a migration version, clear `dirty`, execute a destructive down
  migration, restore over the primary database, or use production user rows as
  test fixtures.
- A successful migration receipt does not declare checkout or entitlement
  acceptance and does not by itself authorize image deployment.
