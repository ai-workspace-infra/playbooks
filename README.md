# playbooks

## Cloud Dev Desktop

The cloud dev desktop flow lives here as two playbooks:

1. `bootstrap_cloud_dev_desktop.yml`
2. `destroy_cloud_dev_desktop.yml`

`bootstrap_cloud_dev_desktop.yml` now includes the create/bootstrap/verify sequence in one entry point. The control-plane repo calls these playbooks from `../playbooks`.

## Traffic Billing Stack

The traffic billing stack now has a single aggregate playbook:

`deploy_svc_plus_core_services_stack.yml`

It orchestrates these existing playbooks in dependency order:

1. `deploy_billing_service.yml`
2. `deploy_xworkmate_bridge_vhosts.yml`
3. `deploy_xray_exporter.yml`
4. `deploy_agent_svc_plus.yml`
5. `deploy_accounts_svc_plus.yml`
6. `deploy_stunnel-client.yml`
7. `deploy_apisix.yml`
8. `deploy_console_svc_plus.yml`

### Full stack deploy

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
export STACK_TARGET_HOST=jp_xhttp_contabo_host
export console_service_sync_dns=true
ansible-playbook -i inventory.ini deploy_svc_plus_core_services_stack.yml
```

`STACK_ENV_FILE=./.env` is optional. Use it when you want the aggregate playbook to read a local `.env` file; GitHub Actions or other CI runners can skip it and pass values with `-e` instead.

### Deploy to one target host directly

Use `STACK_TARGET_HOST` to override the stack host groups when you want all services to target the same inventory host. For console-only runs, use Ansible's `-l jp_xhttp_contabo_host` limit instead of a separate host variable, and keep `console_service_sync_dns=true` if you want DNS reconciliation.

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp_xhttp_contabo_host
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
export console_service_sync_dns=true
ansible-playbook -i inventory.ini -l jp_xhttp_contabo_host deploy_svc_plus_core_services_stack.yml
```

### Deploy only selected services

Use `STACK_SERVICES` with a comma-separated list:

- `billing-service`
- `xworkmate-bridge`
- `xray-exporter`
- `agent`
- `accounts`
- `stunnel-client`
- `apisix`
- `console`

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp-xhttp-contabo.svc.plus
export STACK_SERVICES=xray-exporter,billing-service,agent,xworkmate-bridge
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
ansible-playbook -i inventory.ini -l jp_xhttp_contabo_host deploy_svc_plus_core_services_stack.yml
```

### Notes

- `accounts` and `console` still use their existing role contracts.
- `console` requires `FRONTEND_IMAGE` because the target host only does pull-only compose deployment.
- `console` now writes a Caddy fragment named like `<server-name>-<release_id>-<hostname>-<domain>.caddy` instead of managing the Caddy service container itself.
- `billing-service` requires `DATABASE_URL`.
- `xray-exporter` and `agent` require `INTERNAL_SERVICE_TOKEN`.
- `xworkmate-bridge` accepts `XWORKMATE_BRIDGE_HOSTS`, and also follows `STACK_TARGET_HOST` when you want to deploy the whole stack to one host.

### Deploy console to a specific host and sync DNS

`deploy_console_svc_plus.yml` now accepts `console_service_sync_dns=true` to rebuild and reconcile DNS records after deployment. For host selection, use Ansible's `-l jp_xhttp_contabo_host` limit.

Example:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
ansible-playbook -i inventory.ini deploy_console_svc_plus.yml \
  -e console_service_sync_dns=true \
  -e FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
```
