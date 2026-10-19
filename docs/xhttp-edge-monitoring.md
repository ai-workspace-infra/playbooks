# XHTTP edge monitoring

Ownership: collection is managed by roles/vhosts/vector-agent; dashboard and
Grafana alerts are provisioned by roles/docker/observability-server. No second
Vector instance, manual dashboard import, or separate node inventory is needed.

Enable the optional server-side alert group with
observability_xhttp_alerting_enabled=true. It uses the existing contact policy;
without a configured receiver, rule provisioning does not prove notification
delivery. Initial thresholds: more than two same-class errors in five minutes,
more than two Xray starts in ten minutes, or metrics older than three minutes
for one minute, grouped by instance. Sparse event rules treat no data as OK;
the independent freshness rule monitors telemetry loss.

The canonical deploy_observability_agent.yml enables collection where the
existing Xray detection enables exporters. Direct role callers may opt in with
vector_xhttp_monitoring_enabled=true. It defaults to false for other callers.
Use the IaC-generated CMDB inventory and --limit for the authorized regional
nodes. Credentials use the existing runtime Vault path and authenticated sinks.

Collection reads Caddy's loopback admin metrics and journal errors before the
ordinary reverse-proxy log filter. Bounded event labels distinguish socket_missing,
unexpected_eof, upstream_reset, connection_refused, other_502, and xray_started. PID 1 Started
events include explicit restarts that systemd NRestarts misses. Journal delivery
is checkpointed; metric conversion is not an exactly-once audit ledger.

For a full install use the existing agent and server entrypoints. For installed
hosts use deploy_xhttp_monitoring.yml, which invokes tasks owned by those same
roles, serializes edge updates and preserves installed billing pipelines and
credentials. If an edge has no remote-write sink, read dedicated monitoring
credentials from kv/data/CICD/observability using the controller's runtime Vault
token. Never substitute the billing/internal token. Run --syntax-check and --check
before applying. Vector changes use the existing role handler; no Xray restart
is needed. Server provisioning follows the existing Grafana handler.
For a snapshot-only relay with no monitoring sink, verify both installed Xray
exporters on loopback ports 8080/8081 before adding the shared Xray scrape
fragment. Existing full monitoring configurations retain their Xray sources.

The narrow entrypoint enables Caddy per-host HTTP metrics through the owning
Caddy role, validating and gracefully reloading only when necessary. Binary
paths are discovered on each host; installed Vector config ownership is retained
instead of assuming a package-managed vector user. Journal counters start from
new events (since_now=true) to avoid treating deployment-time history replay as
current failures. SSH targets must come from the existing inventory/CMDB or the
explicit user-supplied host selection; do not commit a parallel fleet inventory.

Acceptance: validate Vector configuration; verify authenticated backend queries
for caddy_config_last_reload_successful{job="xhttp-caddy"}; verify dashboard UID
xconnect-comprehensive (XConnect 综合监控); inspect alert provisioning. An absent event series must
not be displayed as zero. HTTP rate/TTFB panels require ingress HTTP metrics;
the admin metrics endpoint alone does not prove these series exist. No dashboard
panel claims socket readiness or authenticated tunnel health. Add an independent
credential-backed tunnel probe before claiming end-to-end availability.

For a full install rollback, disable vector_xhttp_monitoring_enabled and reapply
the normal agent role. For a narrow update rollback, restore the protected Vector
configuration backup made by the copy task and restart Vector through Ansible.
The narrow entrypoint requires explicit enablement and does not uninstall data
paths. Disable observability_xhttp_alerting_enabled in the server role to remove
the dedicated alert file.
Dashboard deletion follows the existing provisioning provider deletion policy.
This source change does not itself constitute deployment or live acceptance.

## Comprehensive dashboard

The comprehensive UID is `xconnect-comprehensive`, titled
`XConnect 综合监控`. Sections follow an overview-to-detail workflow: Xray
traffic and availability, XHTTP failures/restarts/collection freshness, then
the secure Email/Proxy UUID Trace lookup entry. A top-level shortcut opens
the existing diagnostic app. This is a linked secure lookup flow, not an
embedded identity form or a new Trace correlation implementation.

All charts share the dashboard time range and node selector. Job filters
apply only to Xray. The existing Xray user metric selector is excluded from
URL synchronization; identity resolution remains a POST to the diagnostic app.
Node context must not be presented as evidence belonging to an individual Trace.
The existing `begqoward2epsf`, `xhttp-edge-reliability` and
`xconnect-user-connection-diagnostics` UIDs retain their original independent
dashboards and content. The comprehensive dashboard is additive; no dashboard
is replaced by a navigation-only placeholder.
