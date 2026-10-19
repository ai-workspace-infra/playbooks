# XConnect diagnostics on the existing monitoring stack

Use `deploy_observability_diagnostics.yml` with `observability_accounts_hosts`
and the existing `observability_hosts` inventory. It calls the existing
Accounts runtime role and observability server role, not a second server or
collector. Accounts must already run the identity resolver API. No application
image is rebuilt or upgraded; the Accounts image digest must match its running
Compose declaration. Only Accounts and Grafana may be recreated.

The existing monitoring record `kv/data/CICD/observability` remains the source
for `user` and `password`. The focused Accounts repair may provision a missing
`IDENTITY_LOOKUP_TOKEN` field using a compare-and-set write that preserves all
existing fields. Agents still receive only the existing ingest user/password;
the identity token is not copied to them. The legacy diagnostics Vault path can
still be selected explicitly with `observability_xconnect_diagnostics_vault_path`.
Never reuse an ingest password or `INTERNAL_SERVICE_TOKEN` for identity lookup.

Accounts already consumes `/etc/xcontrol/web-saas/accounts-smtp.env` as an
Accounts-only runtime environment. The repair updates just the resolver key in
that file, retaining SMTP. SMTP rotation preserves this separately managed key.
The historical filename is retained to avoid another GitOps mount/config source.
The credential must not be inserted into the database/shared services env file.

The server verifies the resolver accepts its scoped token (an empty POST must
produce authenticated validation status 400, not 401/503) before installing the
pinned/checksummed plugin release. A minimal Compose override preserves the
running Grafana image ID and existing plugin allowlist; only this one plugin is
added. Existing databases, collectors, metrics/log sinks and Caddy are unchanged.
Full server installs and focused repairs call the same plugin lifecycle tasks.
The shared plugin mount root belongs to Grafana UID/GID 472, including when this
app is disabled, so existing plugins can still be installed at container startup.
The named observability host opts in through host vars so a future full server
deployment retains the feature.

Acceptance requires plugin registration, enabled provisioning, authenticated
resource calls, missing-identity handling, upstream failure handling and raw
identity exclusion from URLs/responses. Anonymous resource calls must fail.
Page loading alone does not prove any real connection Trace exists. An empty
Trace result is not zero latency or proof that a user had no connections.

For rollback, restore the protected Accounts env backup and recreate only its
existing pinned service. Restore the prior Grafana provisioning/Compose settings
from the deployment backup and recreate only Grafana using its recorded image.
Removing the override alone does not disable an already provisioned app. Do not
rotate shared ingest credentials as rollback.
