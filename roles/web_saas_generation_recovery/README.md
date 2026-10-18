# New-generation Web SaaS recovery

This Role restores a reviewed custom-format PostgreSQL archive to a newly
provisioned recovery generation, for example V2 → V3 or V3 → V4. It never
connects to the source DB, switches DNS, restores shared Vault, or replaces
an existing database.

The controller authenticates the IaC inventory/receipt, pins a clean GitOps
checkout and immutable tag, and selects one inventory host. The role checks
the CMDB resource name, recovery group, actual host name, persistent data
device and UUID. A normal production host or the source generation fails.

The GitOps manifest binds the source archive checksum, exact clean schema
version and nonempty user/subscription/ledger samples to the destination.
The archive is copied from a private controller staging file (0600) to
a root-owned directory on the destination /data disk (0700, archive 0600).
The original historical archive is not claimed to be encrypted. Transport
must use authenticated SSH. The caller deletes its temporary copy after
successful destination verification; the source archive remains untouched.

Phases in web-saas-generation-recovery.yml:

1. preflight: verify contracts and runtime disk/host facts.
2. prepare: install the existing Docker role with its optional OS baseline
   disabled; obtain recovery-only entropy through Vault sys/tools/random;
   render independent credentials; validate complete Compose; select the
   fixed storage tag/target in Doco-CD and wait for PostgreSQL.
3. restore: accept only a previously absent account database. Restore in a
   transaction without --clean or ownership/ACL cloning. Record physical DB
   identity, checksum, exact migration state, counts and all public rows /
   sequence fingerprint. Interrupted attempts retain the destination for
   investigation; no automatic drop, reset or destructive retry is offered.
4. deploy: first revalidate the completed restore, then select the same
   GitOps tag's application target in Doco-CD.
5. verify: compare identity, migration state, samples and full data/sequence
   digest with the completed checkpoint, including after application startup.

Re-execution after a completed restore verifies the checkpoint instead of
reapplying SQL. A nonempty/unrecorded destination is rejected. Applications
have background writers disabled, independent auth credentials and no
Stripe/OAuth/SMTP credentials. The recovery DB application role has
default_transaction_read_only=on. Isolated application networks block
outbound external traffic; ingress binds only to loopback and accepts
read-only probes. Local Caddy certificates are recovery-specific. Do not
interpret readiness as authenticated login, payment or production cutover
acceptance.

Required caller variables: web_saas_recovery_target,
web_saas_recovery_manifest_file, web_saas_recovery_gitops_checkout,
web_saas_recovery_gitops_sha and web_saas_recovery_phase. Restore additionally
requires web_saas_recovery_controller_archive. Inventory uses the existing
inventory/terraform_cmdb.py with AI_WORKSPACE_CMDB_JSON pointing to the
authenticated IaC artifact. SSH identity overrides are runtime authentication
inputs, not a replacement CMDB.

Cloud resources/DNS remain IaC responsibilities. Application release
declarations belong to GitOps. Only the destination host is writable by
this Role; source snapshot transport is an explicit read-only SSH operation.
