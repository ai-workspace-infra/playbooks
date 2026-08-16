# UAT Vector -> Billing fan-out

## Configuration

Set these only for the UAT observability-agent deployment:

```text
VECTOR_BILLING_INGEST_ENABLED=true
VECTOR_BILLING_INGEST_ADDRESS=127.0.0.1:8686
VECTOR_BILLING_INGEST_URL=https://billing-uat.onwalk.net/v1/ingest/snapshots

## UAT host-group and Stats API correction

The generated UAT inventory puts the agent host in `agent_proxy`; it is not
guaranteed to be present in the legacy `xray_exporter` group. The Vector
template therefore uses the same local `pgrep -x xray` signal that gates the
exporter role, while retaining the legacy group as a fallback for standalone
inventory runs.

The UAT `agent-proxy` Xray instances expose Stats APIs on
`127.0.0.1:28080` and `127.0.0.1:28181`. Exporter addresses are now explicit
and remain overrideable with `XRAY_EXPORTER_XRAY_API_ADDR` and
`XRAY_EXPORTER_TCP_XRAY_API_ADDR` for hosts with the legacy layout.

The Billing HTTP sink uses a disk buffer. Vector requires the configured
`max_size` to be at least `268435488` bytes, so the buffer is set just above
the 256 MiB boundary; otherwise Vector keeps restarting and never sends
accepted snapshots.
VECTOR_SNAPSHOT_URL=http://127.0.0.1:8686
BILLING_INGEST_MODE=push
INTERNAL_SERVICE_TOKEN=<Vault injected shared token>
```

The Vector source is loopback-only. The external Billing Caddy route remains
restricted to the configured UAT agent CIDR, while Billing itself validates the
same Bearer token as Accounts on the ingest endpoint.

## Fan-out boundaries

- Snapshot HTTP input -> Billing HTTP sink -> PostgreSQL write path.
- Prometheus scrape input -> existing Observability remote-write sink ->
  Grafana dashboards.
- Billing failures are buffered/retried by the Billing sink and do not disable
  the Prometheus/Grafana path.

## Verification

Render and validate Vector TOML before deployment. Then check the exporter
unit, Vector logs, authenticated Billing `/v1/status`, PostgreSQL minute
buckets/ledger, Accounts usage summary, and the Portal account panel in that
order. Do not point this configuration at production domains.
