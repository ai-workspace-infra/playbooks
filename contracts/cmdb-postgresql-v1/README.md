# CMDB PostgreSQL contract v1

This directory defines the first PostgreSQL schema contract for the dynamic
CMDB described in the multi-cloud CMDB plan. It is intentionally separate from
the legacy `cmdb.json` deployment artifact consumed by `terraform_cmdb.py`.

## Apply boundary

`migrations/001_initial_schema.sql` is a migration artifact, not an automatic
database initializer. Before applying it, the operator must explicitly select
the target environment/database, provision the dedicated `cmdb` database and
roles from the approved secret-management workflow, and take a recoverable
backup. The database engine/endpoint is not selected by this contract. Ordinary
deploy and probe runs must not create or mutate the database.

Run migrations only with the controlled migration identity after it has been
authorized to `SET ROLE cmdb_owner`. The migration expects `cmdb_owner`,
`cmdb_writer`, `cmdb_reader`, and `cmdb_auditor` roles to exist. It creates the
`cmdb` schema and objects, grants collector writes only
to the required tables, and exposes read-only views to Grafana/auditors. It
does not create login roles, set passwords, or grant access to another database.

## Data safety

Provider payloads must be allowlisted and scrubbed before insertion. Never put
tokens, credentials, private keys, environment variable values, startup scripts,
or unfiltered user data in `raw_snapshots`. A failed or partial collection must
not be represented as an empty complete scope. Resource absence is only a
candidate until a complete scope and provider-specific deletion confirmation
are recorded.

The migration is additive and has no destructive down migration. Schema changes
must use a new numbered migration and be forward-compatible with the deployed
writer and Grafana views.

## Current limitations

This contract does not provision a PostgreSQL engine, select `postgresql.svc.plus`,
deploy collectors, or claim live inventory. Provider API coverage, database
endpoint/CA, role credentials, backup policy, and UAT migration evidence remain
separate rollout gates.

## Grafana integration

The observability-server role ships a CMDB dashboard and an optional
`cmdb_postgres` datasource. The datasource defaults off; enabling it requires an
explicit host, a verified PostgreSQL TLS route, and `CMDB_READER_PASSWORD` at
the separate `kv/data/observability/cmdb-grafana` Vault path. The generated
runtime environment file is root-owned mode `0600`, and the Grafana SQL queries
use only the read-only views except the monitoring coverage aggregate. The
existing homepage's dashboard-list panel discovers the CMDB dashboard after
Grafana provisions it; the existing default dashboard itself is not replaced.
