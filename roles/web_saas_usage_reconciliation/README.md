# Controlled usage reconciliation

Owner: Playbooks. Caller: `web-saas-generation-usage-reconciliation.yml`.
This initial audited lane is V2 (read-only database source) → V3 (only SSH target).
It does not stop/start services, change DNS, connect by SSH to V2, or run DDL.

Supply IaC-generated inventory with only the exact target selected; force SSH
host-key checking in the maintenance invocation (the legacy repository-wide
Ansible defaults are not safe defaults for this run). Supply Vault-resolved
secrets at runtime with `no_log`, never in argv or committed vars.

Before invoking, quiesce all target writers and Doco-CD through the reviewed
maintenance procedure. Supply the complete `web_saas_usage_reconciliation_writers`
container-name list; stopped state is checked before backup and before execution.
The operator must account for external writers; this role cannot discover them.

Use the existing generation data-operation inputs for backup/baseline/restore.
Set operation request container to `web-saas-accounts` (backup-restore contract).
Also supply a target-host Linux static `migratectl` path, its SHA-256, compiled
schema/Billing checksums and the read-only source DSN through role inputs.
Build/stage the binary off-host using its reviewed source SHA; no host compilation.

For an isolated PostgreSQL container, opt in to
`web_saas_usage_reconciliation_execution_mode: host-loopback`. The same pinned
binary runs on the target host; the source URL must use `127.0.0.1` and database
`account` through an already authenticated SSH reverse forward. The role does not
create tunnels or change source networking. It resolves target IP and existing
PostgreSQL credentials in memory from the single running local container and
requires the `/data/postgresql` bind mount. No password is placed in argv, files
or evidence. Default container execution is unchanged. Both modes retain the
same backup/restore and transaction/conflict gates.

Default is preview only. `web_saas_usage_reconciliation_apply=true` explicitly
enables the transaction after this invocation's encrypted backup and isolated
restore. The executor recomputes the preview and binds apply to its exact hash;
source movement or conflicts stop execution, without automatic retries.
Quota cannot overwrite target-only financial activity; schema and protected
tables remain unchanged. Missing users or incompatible profile facts fail closed.

Each run uses a fresh numeric run ID and immutable evidence paths. Backup files
stay under `/data/backups/web-saas/<environment>/<release>/<run>/`, private.
Aggregate result is `web_saas_usage_reconciliation_evidence`; no row data or DSNs
are printed. Any failure leaves writers paused for operator inspection; never
automatically restore over the target or resume before runtime acceptance.

Local synthetic tests are not V3 acceptance. No live merge has been executed by
adding this role. Source writes continue, so acceptance is snapshot-bound, not
a promise of indefinitely real-time equality.
