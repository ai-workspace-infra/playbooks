# postgresql_service

Managed single-host deployment for `postgresql.svc.plus`.

This role reconciles the current production shape:

- local PostgreSQL container on `127.0.0.1:5432`
- public TLS tunnel via `stunnel` on `0.0.0.0:5433`
- shared Docker network `cn-toolkit-shared`

Primary entrypoint:

- `deploy_postgresql_svc_plus.yml`

Common overrides:

- `POSTGRESQL_POSTGRES_IMAGE_REPO`
- `POSTGRESQL_POSTGRES_IMAGE_TAG`
- `POSTGRESQL_POSTGRES_PULL_IMAGE`
- `POSTGRESQL_STUNNEL_IMAGE_REPO`
- `POSTGRESQL_STUNNEL_IMAGE_TAG`
- `POSTGRESQL_STUNNEL_PULL_IMAGE`
