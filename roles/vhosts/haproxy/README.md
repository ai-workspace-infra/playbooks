# HAProxy 3.0 Vhost Role

Provisions HAProxy 3.0 as a zero-GC TLS/HTTP2 reverse proxy compatible with XConnect / Xray XHTTP (`h2c` over Unix Domain Socket) and built-in Prometheus exporter (`127.0.0.1:8404/metrics`).

## Key Variables

- `haproxy_vhosts`: List of accepted hostnames sharing the wildcard TLS certificate.
- `haproxy_xhttp_unix_socket`: Path to Xray XHTTP Unix Domain Socket (default `/dev/shm/xray.sock`).
- `haproxy_xhttp_local_weight`: Weight assigned to the local `xray` Unix socket backend when `haproxy_xhttp_spillover_servers` is non-empty (default `100`).
- `haproxy_xhttp_spillover_servers`: Optional list of remote H2/TLS spillover peers for `be_xray_xhttp` zero-downtime traffic draining (default `[]`). Each item supports `name`, `address`, `ssl` (default `true`), `verify` (default `none`), `sni`, `check_inter` (default `5s`), `fall` (default `3`), `rise` (default `2`), and `weight` (default `0`). When non-empty, `be_xray_xhttp` enables `balance roundrobin` so weights can also be adjusted live via `/run/haproxy/admin.sock`.
- `haproxy_stats_listen`: Prometheus `/metrics` & `/stats` bind address (default `127.0.0.1:8404`).
- `haproxy_replace_caddy`: Set `true` to stop/disable `caddy` and `fluxgate` while leaving `/etc/caddy` configs untouched as fallback.

## Reload Policy

- `global` configures `stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners` so reloads preserve active listeners and long-lived XHTTP/H2 connections.
- All configuration changes after initial service bootstrap (`state: started` in `tasks/service.yml`) MUST trigger `Reload haproxy` (`state: reloaded`) after `haproxy -c -f` validation — never `Restart haproxy` (`state: restarted`), which would drop existing connections.
