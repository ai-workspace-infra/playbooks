# PROD native empty database initialization

Playbooks owns target host execution. Toolkit must first bind the immutable
caller, reviewed image/SQL manifest, successful standby/cleanup evidence,
original IaC CMDB and independent production data approval; IaC opens and
revokes same-run access. The existing UAT initialization path is unchanged.

Preview is the default. An absent `account` database is not created during
preview. Apply creates only an absent account DB and invokes the Accounts-owned
prebuilt `migratectl init` with exact SQL hash, bounded transaction/advisory lock,
and paused writers. An existing DB with application objects is always refused.
Failure can leave a newly created empty DB; it is never dropped or reset.

The exact digest image's `/usr/local/bin/migratectl` overrides its server
entrypoint. Manifest qualification uses no network or production volume.
Initialization uses only the existing PostgreSQL container's network and a
connection delivered through SSH-pipelined module stdin and container stdin. No
DSN/password enters a host file, argv or Docker create environment configuration.
Registry authentication uses a private verified tmpfs directory removed even on
login/pull/tool failure. Raw command output
and credentials are never logged. No source connection, application container,
seed, copy, Billing extension, or gateway/CNAME mutation occurs here. A success
receipt reports zero business rows and `database_cutover_approved=false`.
