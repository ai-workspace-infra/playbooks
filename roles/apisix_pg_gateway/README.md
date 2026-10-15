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
- `GATEWAY_CONFIG_DB_HOST`, with optional `GATEWAY_CONFIG_DB_PORT` and
  `GATEWAY_CONFIG_DB_NAME`

The role derives the restricted runtime role password in memory, applies the
additive `gateway_config` schema through the migration DSN, and writes only
short-lived derived files to the tmpfs runtime directory. Secret values are
never stored in GitOps, command arguments, or the repository.

The role installs two generic service units. The environment-specific values are
rendered into `gateway_environment_file`; the service names remain the same in
UAT and other environments:

```text
xconnect-apisix.service
  ExecStartPre: prepare_runtime.py (remote PG schema and runtime credentials)
  -> xconnect-apisix-config.service
       config-supervisor.py
       ├─ gateway_config.py serve
       └─ gateway_config.py sync
```

`xconnect-apisix-config.service` treats the API and synchronizer as one failure
domain: if either child exits, the wrapper terminates its sibling and exits so
systemd restarts both together. `GATEWAY_ENVIRONMENT`, `GATEWAY_REGION`, and
`GATEWAY_ROOT` are supplied through the environment file; no UAT-suffixed unit
names or runtime IP/secret values are stored in Git.

The synchronizer atomically replaces the full `apisix.yaml`; APISIX keeps the
upstream YAML file watcher and hot reload behavior.
