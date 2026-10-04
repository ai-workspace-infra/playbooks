# Web SaaS release upgrade role

`web-saas-release-upgrade.yml` calls this role on the exact `web-saas-<env>`
host selected by a reviewed CMDB inventory. It currently owns two reusable
phases: `preflight` and `backup`. Other release phases require separate reviewed
implementation; this role does not claim that an application deployment or
schema migration took place.

Required variables: `web_saas_release_target_host`,
`web_saas_release_gitops_host_verified`, `web_saas_release_environment`,
`web_saas_release_mode`, `web_saas_release_phase`, `web_saas_release_tag`,
`web_saas_release_run_id`, `web_saas_release_expected_version`,
`web_saas_release_candidate_sha256` and `web_saas_release_receipt_file`.
The `backup` phase also requires `web_saas_release_backup_passphrase` from
the selected environment's Vault role. No password or DSN belongs in GitOps,
workflow inputs, inventory or public receipts.

The role checks that `/data` is a separate mount and writes one immutable,
encrypted archive to
`/data/backups/web-saas/<environment>/<release-tag>/<run-id>/account.dump.enc`
with a private directory. It restores the archive to a new `release_verify_<run-id>`
database on the same PostgreSQL instance, compares the clean migration version
and nonempty user/subscription samples, then drops only that generated test
database. The serving `account` database is never used as the restore target.
The archive remains on the environment's web-saas host for recovery.

Receipts describe only the database component. The release control plane must
combine them with separately executed original-account login, permission,
entitlement, image digest and application health evidence. A successful role
run alone is not UAT business acceptance or PROD promotion authorization.

Run `ansible-playbook --syntax-check -i 'web-saas-uat,' web-saas-release-upgrade.yml
-e 'web_saas_release_target_host=web-saas-uat'` after editing the role.
