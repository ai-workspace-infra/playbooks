# Canonical business database identity

GitOps `compose/web-saas/runtime/prod.yaml#business_database` selects
`account_user` and references the existing environment-scoped password. Only
the controller reads Vault at execution; GitOps contains no password or DSN.
Accounts and Billing consume the same protected raw `business-database.env`.
No alternative business role or credential is generated during recovery.

Run `web-saas-business-database.yml` with one verified target, clean pinned
GitOps checkout/SHA, `web_saas_business_database_confirm: true` and the populated
target `web_saas_business_database_backup_manifest`. The manifest must prove a
successful isolated restore with equal data fingerprints before role changes.
`prepare` creates a missing native role using the existing Vault password,
grants business DML and owned-sequence USAGE/SELECT, and verifies TCP SCRAM
authentication using a disposable client on the existing internal network.
It refuses an existing credential mismatch; no password rotation/fallback.
Schema migration tables, DDL, ownership and administrative grants are excluded.
RLS bypass is the explicitly declared existing native backend contract.

Preparation does not restart services, enable background writers, select CD,
or change DNS. Deploy the pinned Compose declaration through the existing CD
selector, preserving source/target writer ownership. `retire` then verifies both
running app identities and absence of legacy DB sessions before disabling login
for obsolete roles; it never drops roles, tables or their owned objects.
The dedicated audit reader is not modified. Failure preserves all backups/data.

Recovery: before new target writes, retain the old pinned app/config as an
operator-reviewed checkpoint. Never automatically reopen an old writer or
overwrite a business DB. Unexpected ACLs or existing password mismatch stop
maintenance for review, not a broad GRANT or forced password reset.
