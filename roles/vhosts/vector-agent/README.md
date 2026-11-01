# Vector agent

The `vhosts/vector-agent` role installs and configures the host Vector agent for
metrics and logs collection to the configured observability endpoint.

## Caddy metrics

Set `vector_caddy_metrics_enabled: true` only on hosts where Caddy's admin API
is bound to loopback and Caddy metrics collection is enabled. By default the
role scrapes `http://127.0.0.1:2019/metrics` every `vector_scrape_interval`
and sends the labeled series through the existing authenticated
Prometheus remote-write sink. The metrics endpoint is not exposed publicly.

The default is disabled so hosts without Caddy do not receive a failing scrape.
Set `vector_caddy_metrics_endpoint` only when the local Caddy admin listener
uses a different loopback address or port.

## Rollback

Set `vector_caddy_metrics_enabled: false` and redeploy the role. The Caddy
metrics source and transform are removed from the rendered Vector config; the
existing shared remote-write sink remains in place.
