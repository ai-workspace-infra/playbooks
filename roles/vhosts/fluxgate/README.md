# Fluxgate Vhost Role

Deploys pre-built **Fluxgate** (Rust + Cloudflare Pingora) release binaries to edge nodes without compiling on target hosts, configures Caddyfile-style vhosts (`h2c` over UDS + exporter prefix routing), enables the built-in Prometheus `/metrics` endpoint (`127.0.0.1:9102/metrics`), and validates configs via `fluxgate validate`.

## Key Variables

- `fluxgate_binary_src` / `fluxgate_download_url`: Local CI artifact path or release URL (`amd64` / `arm64`).
- `fluxgate_vhosts`: List of virtual host names sharing the TLS certificate.
- `fluxgate_metrics_listen`: Prometheus `/metrics` address (default `127.0.0.1:9102`).
- `fluxgate_replace_caddy`: Set `true` to stop/disable `caddy` and `haproxy` while preserving `/etc/caddy` configs untouched as fallback.
