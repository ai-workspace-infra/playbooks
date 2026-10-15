# APISIX regional edge gateway role

This role deploys APISIX Standalone, the PostgreSQL JSONB configuration API and
the synchronizer directly on an edge node with systemd. It does not use DocoD,
Docker Compose, or etcd.

The APISIX binary and its native runtime are expected to be installed already
at `gateway_apisix_home`. The pinned APISIX fork checkout supplies the
synchronizer and validator through `gateway_source_path`.

Before running the role, Vault Agent must provision
`gateway_secret_env_file` on the edge node with runtime-only values:

- `INTERNAL_SERVICE_TOKEN`
- `GATEWAY_MIGRATION_DSN` for the remote web-saas PostgreSQL `account` database
- `GATEWAY_CONFIG_DB_HOST`, with optional `GATEWAY_CONFIG_DB_PORT`,
  `GATEWAY_CONFIG_DB_NAME` and `GATEWAY_CONFIG_DB_USER`

The role derives the restricted runtime role password in memory, applies the
additive `gateway_config` schema through the migration DSN, and writes only
short-lived derived files to the tmpfs runtime directory. Secret values are
never stored in GitOps, command arguments, or the repository.

The normal dependency chain is:

```text
remote PG preparation
  -> xconnect-apisix-uat.service
  -> xconnect-apisix-uat-config-api.service
  -> xconnect-apisix-uat-config-sync.service
```

The synchronizer atomically replaces the full `apisix.yaml`; APISIX keeps the
upstream YAML file watcher and hot reload behavior.
