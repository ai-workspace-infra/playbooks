# XConnect observability collector

This OS-level role writes a small, non-sensitive Prometheus textfile for a
Gateway or One node. It is intentionally paired with the existing reusable
`node_exporter`, `process_exporter`, and `vector-agent` roles.

The resulting path is:

```text
Gateway / One runtime
  -> /var/lib/node_exporter/xconnect.prom
  -> node_exporter (localhost:9100)
  -> Vector remote write
  -> https://observability.svc.plus
  -> VictoriaMetrics / Grafana
```

The collector contains no transport credential, WireGuard private key, VLESS
identifier, or Zero token. Authentication for Vector is supplied only at
deployment time from `kv/data/CICD/observability`.

It emits `xconnect_runtime_info`, `xconnect_runtime_up`,
`xconnect_wireguard_peer_count`, and
`xconnect_wireguard_latest_handshake_age_seconds`, labeled by role,
environment, and stable node identity.
