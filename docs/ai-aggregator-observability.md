# AI Aggregator monitoring

The AI Aggregator dashboard combines request-level metrics from APISIX and
LiteLLM, local reachability probes for each service, and host saturation from
the existing Node Exporter. It uses the existing Vector -> authenticated
Prometheus remote-write -> VictoriaMetrics path; no second metrics backend or
public exporter listener is introduced.

## Collection plan

| Signal | Source | Labels / dimensions |
| --- | --- | --- |
| Request count and response status | APISIX built-in `prometheus` plugin, applied as a global rule | APISIX route/service/model/status labels, plus `instance`, `environment`, `job=ai-aggregator`, `component=apisix` |
| Request and upstream latency | APISIX `apisix_http_latency` histogram | latency type, route/service, status; use histogram buckets for p95/p99 |
| Prompt/completion tokens and LLM latency | APISIX AI proxy Prometheus metrics when the active plugin records usage | effective/requested model only where available; no consumer identity |
| LiteLLM requests and tokens | LiteLLM Prometheus callback and native `/metrics` endpoint | Caller identity labels excluded; bounded host/environment/component labels |
| Service reachability | Existing Blackbox Exporter | one bounded `service` label per APISIX, New API, LiteLLM and CPA instance |
| CPU, memory, disk, network, process | Existing Node Exporter and Process Exporter | host and environment |

APISIX metrics listen on `127.0.0.1:9091` at
`/apisix/prometheus/metrics`. The APISIX proxy remains bound to loopback on
`9080`; the exporter is not routed through Caddy. LiteLLM's Prometheus callback
is enabled in its config and scraped at `127.0.0.1:4000/metrics`. Its listener
is loopback-only and Caddy does not route the path. The endpoint allows
unauthenticated local scraping so the Vector config does not need the
LiteLLM master key. Change the Vector target variables in host inventory if
the declared ports differ. The LiteLLM runtime must include its documented
`prometheus_client` dependency.

Set `vector_ai_aggregator_enabled: true` on the aggregator host to enable the
application scrapes and probes. The default probes check New API
`/api/status` for HTTP 200 and TCP reachability for APISIX, LiteLLM and CPA
ports `8317`–`8320`. Override `vector_ai_aggregator_metrics_targets` and
`vector_ai_aggregator_probe_targets` in inventory when instance IDs, ports or
health paths differ. The standalone agent entrypoint accepts
`VECTOR_AI_AGGREGATOR_ENABLED=true` to opt in; set
`VECTOR_AI_AGGREGATOR_PUBLIC_TARGET=https://<aggregator-hostname>` to add an
independent HTTPS/TLS probe for Caddy. Blackbox probes contain no credentials
and do not call a model provider.

The AI Aggregator Grafana dashboard JSON is discovered and provisioned by the
existing observability server role. Its variables select `environment` and
`instance`; the default selections are `uat` and `ai-aggregator-uat-01`.

## Alerting and interpretation

- Alert on sustained gateway 5xx ratio, user-facing p95 latency, or a failed
  external HTTPS probe. Tune thresholds to the UAT traffic baseline.
- Treat `probe_success` as process/endpoint reachability only. It does not
  verify OAuth refresh, provider account health, token accounting, or a
  complete inference response.
- APISIX HTTP histograms are in milliseconds; the dashboard converts them to
  seconds. APISIX LLM latency uses `type="total"` and `type="ttft"`; TTFT is
  recorded only for streaming requests. LLM latency and token distribution
  metrics require an APISIX runtime that includes them (documented in APISIX
  3.18+), and appear only after AI traffic.
- APISIX `llm_active_connections` is approximate with `ai-proxy-multi` fallback
  retries; do not use it as a strict alert signal.
- LiteLLM series require the Prometheus callback and its metrics dependency.
  Empty panels are not proof of zero provider usage.
- Add an external blackbox target for the public Caddy hostname from a separate
  monitoring node. A probe running on the aggregator host cannot establish
  independent internet reachability.

## Cardinality and privacy

Do not add API keys, usernames, request IDs, prompt text, raw URL paths, or
provider account identifiers as metric labels. Keep provider credentials and
OAuth state in Vault and the existing runtime paths. Use logs for request-level
diagnostics with the current access controls and retention policy.
