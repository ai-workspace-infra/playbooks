# PostgreSQL exporter

This role installs `prometheus-community/postgres_exporter` as a localhost-only
systemd service and connects it to the local PostgreSQL deployment.

The role is safe to include for every host. It enables itself only when one of
the following is detected:

- a native `postgres` process;
- a local listener on port `5432` or `15432`; or
- a running Docker image matching `postgres`, `postgis`, or `pgvector`.

For PostgreSQL 10 and newer it creates/updates the `postgres_exporter` database
role, grants `pg_monitor`, and grants `CONNECT` on connectable databases. The
generated password is persisted at `/etc/postgres_exporter/password` with mode
`0600`; it is never written to Git.

After `http://127.0.0.1:9187/metrics` returns HTTP 200, the role sets
`vector_postgres_exporter_enabled=true`. Vector then scrapes the endpoint and
remote-writes the metrics with `job="postgres"` and the inventory hostname as
`instance`.

The default exporter release is v0.19.1. Override
`postgres_exporter_version` together with `postgres_exporter_checksum` when
using another release. For port-only PostgreSQL detection, provide the admin
password through `POSTGRESQL_ADMIN_PASSWORD`; the deployment workflow obtains
that value from the environment-scoped Vault database path.
