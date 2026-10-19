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
   render independent database/login credentials and read the canonical shared
   service credential from Vault; validate complete Compose; select the
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
have background writers disabled, independent login credentials and no
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

## Shared service authentication

The authoritative PROD source is Vault `kv/prod/accounts/runtime`, key
`INTERNAL_SERVICE_TOKEN`. The pinned GitOps checkout declares that reference
in `compose/web-saas/runtime/prod.yaml`; no generation name is part of the path.
Missing or invalid values stop preparation/deployment; random fallback and
Vault writes are not performed. New database/login secrets remain isolated.

The Role writes only this key to mode-0600
`/etc/xcontrol/web-saas-recovery/internal-service.env`. GitOps loads it last
for Accounts/Billing and the Sandbox override, and loads only this file for
Console, so Console does not inherit database or Stripe secrets. Existing
`secrets.env` and `stripe-sandbox.env` are retained; their legacy service token
is overridden by the dedicated file. Repeating preparation uses the existing
Vault value and preserves database credentials. Verification compares the
three running containers against Vault without outputting values or hashes.

Deploy the matching GitOps changes together with this Role: prepare the file
before selecting the new immutable release. Credential provisioning does not
restart services by itself; Doco-CD applies the changed environment-file
declaration. Verify must fail if the old containers still carry stale tokens.
Keep the previous release available for application rollback.

Agents using the shared fallback credential must obtain `agent_api_token`
from this same Vault key in their authorized deployment caller. Agents with
separate explicitly scoped credentials keep those credentials. This recovery
Role does not reconfigure or restart remote Agents, transfer their endpoints,
or grant new scopes; fresh status reports must be separately verified before
public traffic cutover.

For an existing recovery generation use `web-saas-service-token.yml` with
`web_saas_service_token_generation=prod-vN`, an exact clean GitOps checkout
and SHA, and an IaC inventory limited to that host. Run
`web_saas_service_token_operation=prepare` before selecting the immutable
Doco-CD release, then `web_saas_service_token_operation=verify` afterwards.
The verification operation reads Vault and running containers only; it does
not rewrite the credential file. This entry does not rerun database recovery,
Docker installation, or the historical snapshot preflight.

Cloud resources/DNS remain IaC responsibilities. Application release
declarations belong to GitOps. Only the destination host is writable by
this Role; source snapshot transport is an explicit read-only SSH operation.
