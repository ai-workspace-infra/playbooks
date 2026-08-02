# UAT Vector -> Billing fan-out

## Configuration

Set these only for the UAT observability-agent deployment:

```text
VECTOR_BILLING_INGEST_ENABLED=true
VECTOR_BILLING_INGEST_ADDRESS=127.0.0.1:8686
VECTOR_BILLING_INGEST_URL=https://billing-uat.onwalk.net/v1/ingest/snapshots
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
