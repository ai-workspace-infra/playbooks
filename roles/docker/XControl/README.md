# XControl Docker role

This role provisions the XControl stack (Postgres, account service, RAG server, dashboard, Redis, and Nginx proxy with Certbot assets). Templates from `templates/` and static assets from `files/` are rendered into `{{ xcontrol_workspace }}` and the Docker Compose stack is started.

## Layout
```
files/
├── docker-compose.yaml
├── config/
│   ├── account.yaml
│   └── server.yaml
├── certbot/
│   ├── conf/
│   └── www/
├── nginx/
│   ├── conf.d/
│   │   ├── accounts.conf
│   │   ├── artifact.conf
│   │   ├── bootstrap-nginx.conf
│   │   ├── default.conf
│   │   ├── homepage.conf
│   │   └── rag-server.conf
│   └── nginx.conf
└── run.sh
```

## Defaults
- `xcontrol_deploy_dir`: `/opt/xcontrol`
- `xcontrol_workspace`: `{{ xcontrol_deploy_dir }}`
- `xcontrol_certbot_domains`: `svc.plus` (comma-separated)
- `xcontrol_certbot_email`: `manbuzhe2009@qq.com`
- `xcontrol_homepage_domain`: `{{ xcontrol_primary_domain }}`
- `xcontrol_homepage_alias_domain`: `www.{{ xcontrol_primary_domain }}`
- `xcontrol_homepage_cn_domain`: `cn-homepage.{{ xcontrol_primary_domain }}`
- `xcontrol_accounts_domain`: `accounts.{{ xcontrol_primary_domain }}`
- `xcontrol_rag_domain`: `rag-server.{{ xcontrol_primary_domain }}`
- `xcontrol_rag_api_domain`: `api.{{ xcontrol_primary_domain }}`
- `xcontrol_artifact_domain`: `dl.{{ xcontrol_primary_domain }}`
- `xcontrol_artifact_cn_domain`: `cn-dl.{{ xcontrol_primary_domain }}`
- `xcontrol_db_host`: `db`
- `xcontrol_db_port`: `5432`
- `xcontrol_db_name`: `xcontrol`
- `xcontrol_db_user`: `xcontrol`
- `xcontrol_db_password`: `xcontrol`
- `xcontrol_account_mode`: `server-agent`
- `xcontrol_account_log_level`: `info`
- `xcontrol_account_auth_enable`: `true`
- `xcontrol_account_public_token`: `xcontrol-public-token-2024`
- `xcontrol_account_refresh_secret`: `xcontrol-refresh-secret-2024`
- `xcontrol_account_access_secret`: `xcontrol-access-secret-2024`
- `xcontrol_account_access_expiry`: `1h`
- `xcontrol_account_refresh_expiry`: `168h`
- `xcontrol_account_server_addr`: `:8080`
- `xcontrol_account_read_timeout`: `15s`
- `xcontrol_account_write_timeout`: `15s`
- `xcontrol_account_public_url`: `https://accounts.{{ xcontrol_primary_domain }}`
- `xcontrol_account_tls_enabled`: `false`
- `xcontrol_account_tls_redirect_http`: `false`
- `xcontrol_account_store_driver`: `postgres`
- `xcontrol_account_db_name`: `{{ xcontrol_db_name }}`
- `xcontrol_account_db_sslmode`: `disable`
- `xcontrol_account_db_max_open_conns`: `30`
- `xcontrol_account_db_max_idle_conns`: `10`
- `xcontrol_account_session_ttl`: `24h`
- `xcontrol_account_session_cache`: `memory`
- `xcontrol_account_smtp_host`: `smtp.example.com`
- `xcontrol_account_smtp_port`: `587`
- `xcontrol_account_smtp_username`: `apikey`
- `xcontrol_account_smtp_password`: `change-me`
- `xcontrol_account_smtp_from`: `XControl Account <no-reply@example.com>`
- `xcontrol_account_smtp_timeout`: `10s`
- `xcontrol_account_smtp_tls_mode`: `auto`
- `xcontrol_account_smtp_tls_insecure_skip_verify`: `false`
- `xcontrol_account_xray_sync_enabled`: `false`
- `xcontrol_account_xray_sync_interval`: `5m`
- `xcontrol_account_xray_output_path`: `/usr/local/etc/xray/config.json`
- `xcontrol_account_xray_template_path`: `account/config/xray.config.template.json`
- `xcontrol_account_xray_validate_command`: `[]`
- `xcontrol_account_xray_restart_command`: `["systemctl", "restart", "xray.service"]`
- `xcontrol_account_agent_id`: `account-primary`
- `xcontrol_rag_server_addr`: `:8090`
- `xcontrol_rag_read_timeout`: `120s`
- `xcontrol_rag_write_timeout`: `120s`
- `xcontrol_rag_public_url`: `https://{{ xcontrol_rag_api_domain }}`
- `xcontrol_rag_allowed_origins`: `["https://{{ xcontrol_rag_api_domain }}", "https://{{ xcontrol_homepage_alias_domain }}", "https://{{ xcontrol_homepage_domain }}", "https://{{ xcontrol_accounts_domain }}", "http://localhost:3000", "http://127.0.0.1:3000"]`
- `xcontrol_rag_auth_enable`: `false`
- `xcontrol_rag_auth_url`: `https://{{ xcontrol_accounts_domain }}`
- `xcontrol_rag_api_base_url`: `https://{{ xcontrol_rag_api_domain }}`
- `xcontrol_rag_public_token`: `xcontrol-public-token-2025`
- `xcontrol_rag_redis_addr`: `""`
- `xcontrol_rag_redis_password`: `""`
- `xcontrol_rag_vectordb_db_name`: `rag`
- `xcontrol_rag_vectordb_sslmode`: `disable`
- `xcontrol_rag_vectordb_pgurl`: `postgres://{{ xcontrol_db_user }}:{{ xcontrol_db_password }}@{{ xcontrol_db_host }}:{{ xcontrol_db_port }}/{{ xcontrol_rag_vectordb_db_name }}?sslmode={{ xcontrol_rag_vectordb_sslmode }}`
- `xcontrol_rag_datasources`: `[{"name": "XControl", "repo": "https://github.com/svc-design/XControl", "path": "docs"}]`
- `xcontrol_rag_sync_repo_proxy`: `""`
- `xcontrol_rag_embedder_provider`: `chutes`
- `xcontrol_rag_embedder_models`: `["bge-m3"]`
- `xcontrol_rag_embedder_baseurl`: `http://127.0.0.1:9000`
- `xcontrol_rag_embedder_endpoint`: `http://127.0.0.1:9000/v1/embeddings`
- `xcontrol_rag_generator_provider`: `chutes`
- `xcontrol_rag_generator_models`: `["deepseek-r1:8b"]`
- `xcontrol_rag_generator_baseurl`: `http://127.0.0.1:11434`
- `xcontrol_rag_generator_endpoint`: `http://127.0.0.1:11434/v1/chat/completions`
- `xcontrol_rag_embedding_max_batch`: `64`
- `xcontrol_rag_embedding_dimension`: `1024`

## RUN

```
ansible-playbook -i inventory.ini deploy_XControl_docker.yaml -e "domain=svc.plus" -D -C -l svc.plus
ansible-playbook -i inventory.ini deploy_XControl_docker.yaml -e "domain=svc.plus" -D -l svc.plus
```
