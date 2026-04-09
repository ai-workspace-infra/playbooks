# Zitadel Docker role

This role provisions a Zitadel stack with Postgres and the login frontend, then exposes both services on localhost-only ports so the host Caddy instance can terminate TLS and reverse proxy traffic for `{{ zitadel_domain }}`.

The previous embedded `nginx/certbot` deployment mode now lives in the separate legacy role `docker/zitadel_legacy`.

## Layout
```
files/
└── run.sh
templates/
├── docker-compose.yaml
└── zitadel-site.caddy.j2
```

## Defaults
- `zitadel_deploy_dir`: `/opt/zitadel`
- `zitadel_workspace`: `{{ zitadel_deploy_dir }}`
- `zitadel_domain`: `auth.svc.plus`
- `zitadel_masterkey`: `MasterkeyNeedsToHave32Characters`
- `zitadel_api_bind_host`: `127.0.0.1`
- `zitadel_api_port`: `19080`
- `zitadel_login_bind_host`: `127.0.0.1`
- `zitadel_login_port`: `19081`
- `zitadel_caddy_conf_dir`: `/etc/caddy/conf.d`
- `zitadel_caddy_fragment_path`: `/etc/caddy/conf.d/zitadel.caddy`

## RUN

ansible-playbook -i inventory.ini deploy_zitadel_docker.yaml -e "domain=auth.svc.plus" -D -C -l auth.svc.plus
ansible-playbook -i inventory.ini deploy_zitadel_docker.yaml -e "domain=auth.svc.plus" -D -l auth.svc.plus
