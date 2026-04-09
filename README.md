# playbooks

## Traffic Billing Stack

The traffic billing stack now has a single aggregate playbook:

`deploy_traffic_billing_stack.yml`

It orchestrates these existing playbooks in dependency order:

1. `deploy_xray_exporter.yml`
2. `deploy_billing_service.yml`
3. `deploy_accounts_svc_plus.yml`
4. `deploy_console_svc_plus.yml`
5. `deploy_agent_svc_plus.yml`

### Full stack deploy

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
ansible-playbook -i inventory.ini deploy_traffic_billing_stack.yml
```

### Deploy to one target host directly

Use `STACK_TARGET_HOST` to override all service host groups with one inventory host.

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp-xhttp-contabo.svc.plus
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
ansible-playbook -i inventory.ini deploy_traffic_billing_stack.yml
```

### Deploy only selected services

Use `STACK_SERVICES` with a comma-separated list:

- `xray-exporter`
- `billing-service`
- `accounts`
- `console`
- `agent`

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp-xhttp-contabo.svc.plus
export STACK_SERVICES=xray-exporter,billing-service,agent
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
ansible-playbook -i inventory.ini deploy_traffic_billing_stack.yml
```

### Notes

- `accounts` and `console` still use their existing role contracts.
- `console` requires `FRONTEND_IMAGE` because the target host only does pull-only compose deployment.
- `console` now writes a Caddy fragment named like `<server-name>-<release_id>-<hostname>-<domain>.caddy` instead of managing the Caddy service container itself.
- `billing-service` requires `DATABASE_URL`.
- `xray-exporter` and `agent` require `INTERNAL_SERVICE_TOKEN`.

### Deploy console to a specific host and sync DNS

`deploy_console_svc_plus.yml` now accepts two useful overrides:

- `console_service_target_host`: inventory host to deploy to, for example `jp_xhttp_contabo_host`
- `console_service_sync_dns=true`: rebuild and reconcile DNS records for that target host after deployment

Example:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
ansible-playbook -i inventory.ini deploy_console_svc_plus.yml \
  -e console_service_target_host=jp_xhttp_contabo_host \
  -e console_service_sync_dns=true \
  -e FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
```
