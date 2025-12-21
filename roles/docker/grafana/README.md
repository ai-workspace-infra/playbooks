# Grafana (Docker)

This role deploys Grafana with Docker Compose, creating a persistent data directory and templating a simple `docker-compose.yaml` into `{{ grafana_workspace }}`.

## Defaults
- `grafana_workspace`: `/opt/grafana`
- `grafana_image`: `grafana/grafana:10.4.6`
- `grafana_domain`: `grafana.svc.plus`
- `grafana_protocol`: `http`
- `grafana_host_port`: `3000`
- `grafana_admin_user`: `admin`
- `grafana_admin_password`: `admin`

## Run

Example playbook execution:

```bash
ansible-playbook -i inventory.ini playbooks/deploy_grafana_docker.yaml -e "domain=grafana.example.com" -l grafana.example.com
```
