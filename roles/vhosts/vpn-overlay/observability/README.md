# XConnect observability collector

This OS-level role writes a small, non-sensitive Prometheus textfile for a
Gateway or One node. It installs only the isolated node exporter and Vector
components required for XConnect; it does not import the platform baseline.

The resulting path is:

```text
Gateway / One runtime
  -> /var/lib/xconnect-node-exporter/xconnect.prom
  -> XConnect node exporter (localhost:19100)
  -> Vector remote write
  -> https://observability.svc.plus
  -> VictoriaMetrics / Grafana
```

The role does not import platform `common`, firewall, SSH-hardening, Blackbox,
or Agent Proxy Xray-exporter roles. The collector contains no transport
credential, WireGuard private key, VLESS identifier, or Zero token.
Authentication for Vector is supplied only at deployment time from
`kv/data/CICD/observability`.

It emits `xconnect_runtime_info`, `xconnect_runtime_up`,
`xconnect_wireguard_peer_count`, and
`xconnect_wireguard_latest_handshake_age_seconds`, labeled by role,
environment, and stable node identity.
