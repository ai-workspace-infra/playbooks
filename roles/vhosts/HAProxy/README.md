# HAProxy 3.0 Vhost Role

Provisions HAProxy 3.0 as a zero-GC TLS/HTTP2 reverse proxy compatible with XConnect / Xray XHTTP (`h2c` over Unix Domain Socket) and built-in Prometheus exporter (`127.0.0.1:8404/metrics`).

## Key Variables

- `haproxy_vhosts`: List of accepted hostnames sharing the wildcard TLS certificate.
- `haproxy_xhttp_unix_socket`: Path to Xray XHTTP Unix Domain Socket (default `/dev/shm/xray.sock`).
- `haproxy_stats_listen`: Prometheus `/metrics` & `/stats` bind address (default `127.0.0.1:8404`).
- `haproxy_replace_caddy`: Set `true` to stop/disable `caddy` and `fluxgate` while leaving `/etc/caddy` configs untouched as fallback.
