# Web SaaS post-deploy readiness

This role runs against one selected Web SaaS host after DNS reconciliation. It
checks the long-running Compose containers and Caddy port bindings, then runs a
best-effort local HTTPS/Caddy diagnostic using the environment-specific Console
hostname. The separate Toolkit public-endpoint step remains the release gate.

Required variable:

- `web_saas_canonical_probe_host`: canonical Console hostname for the selected
  environment.

Optional variables:

- `web_saas_container_ready_timeout_seconds` (default `120`)
- `web_saas_container_ready_poll_seconds` (default `3`)

Entrypoint: `verify_web_saas_post_deploy.yml`. Set
`readiness_target_host` to exactly one inventory host and use `--limit` for the
same host. The `console-assets` one-shot initializer is intentionally excluded
from the list of containers required to remain running.
