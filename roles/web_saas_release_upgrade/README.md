# Web SaaS release upgrade role

`web-saas-release-upgrade.yml` calls this role on the exact `web-saas-<env>`
host selected by a reviewed CMDB inventory. It currently owns two reusable
phases: `preflight` and `backup`. Other release phases require separate reviewed
implementation; this role does not claim that an application deployment or
schema migration took place.

Required variables: `web_saas_release_target_host`,
`web_saas_release_environment`,
`web_saas_release_mode`, `web_saas_release_phase`, `web_saas_release_tag`,
`web_saas_release_run_id`, `web_saas_release_expected_version`,
`web_saas_release_candidate_sha256` and `web_saas_release_receipt_file` remain
the facade inputs. Provenance and sample references are read from
`WEB_SAAS_RELEASE_SOURCE_DATABASE_ID`, `WEB_SAAS_RELEASE_BASELINE_ID`, and
`WEB_SAAS_RELEASE_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID` unless supplied through
the existing Ansible variable interface. The backup passphrase defaults to
`lookup('env', 'WEB_SAAS_RELEASE_BACKUP_PASSPHRASE')`. Callers must provide
these runtime values; missing provenance fails closed. No secret is written to
an extra-vars file, GitOps, inventory, or public receipt.

Workflow mapping: pass the existing facade variables
`web_saas_release_target_host`, `web_saas_release_environment`,
`web_saas_release_mode`, `web_saas_release_phase`, `web_saas_release_tag`,
`web_saas_release_run_id`, `web_saas_release_expected_version`,
`web_saas_release_candidate_sha256`, and `web_saas_release_receipt_file` as
ordinary nonsecret Ansible inputs. `web_saas_release_phase` defaults to
`preflight`; accepted values are `preflight` and `backup`. Export the source,
baseline, and authorized sample identifiers as the three
`WEB_SAAS_RELEASE_*_ID` runtime environment variables above. Export the backup
passphrase only in `WEB_SAAS_RELEASE_BACKUP_PASSPHRASE` for a backup run. The
preflight receipt records database component evidence only; a rejected run
exposes a stable `reason_code` in the Ansible task failure and does not emit a
PASS receipt.

The facade calls three reusable roles in sequence: read-only preflight,
encrypted backup, and independent isolated restore verification. The backup
role requires `/data` itself to be a mountpoint and writes one immutable,
encrypted archive to
`/data/backups/web-saas/<environment>/<release-tag>/<run-id>/account.dump.enc`
with private directories. The separate restore role compares the exact clean
migration version, nonempty user and exact subscription counts (zero is valid
for restore rehearsal), and source/restore schema
fingerprint, then drops only the temporary database created by this invocation.
The serving `account` database is never used as the restore target. These are
database component checks only; a zero subscription count leaves G3 blocked and
row counts do not prove G1/G2/G3 semantics.
The archive remains on the environment's web-saas host for recovery.
For the current UAT candidate, CMDB identifies a GCP Spot host while the
checked-in resource declaration does not declare a separate `/data` disk.
This role deliberately refuses to use the root disk; it must not be invoked
for a real rehearsal until the mounted storage and capacity are verified.

The role checks the exact inventory host name and reads a remote machine ID,
but cannot authenticate where the inventory came from. The calling workflow
must independently verify the GitOps/CMDB artifact's source, environment and
instance identity before invoking Ansible. A caller-supplied `verified=true`
flag is not host-identity evidence.

Receipts describe only the database component. The release control plane must
combine them with separately executed original-account login, permission,
entitlement, image digest and application health evidence. A successful role
run alone is not UAT business acceptance or PROD promotion authorization.

The encrypted archive is retained under `/data`. No rollback action drops the
source database or rebuilds storage. Run
`ansible-playbook --syntax-check -i 'web-saas-uat,' web-saas-release-upgrade.yml`
after editing the role.
