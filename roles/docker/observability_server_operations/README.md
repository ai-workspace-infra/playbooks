# Observability service operations

This role owns the telemetry store migration and service acceptance logic used
by the Toolkit's Observability control workflow. It runs on the GitHub Actions
control runner and connects to the explicit UAT source/target hosts with the
runtime SSH key supplied by Vault. The Toolkit remains responsible for UAT
eligibility, operator confirmations, job ordering, and DNS cutover approval.

The role accepts `observability_operation` values `data_migrate`,
`verify_store`, `verify_target`, `verify_mcp`, or `post_dns_cutover`, and requires
`observability_operations_environment: uat`. Data mutation behavior is
preserved: source snapshots are version checked; target restore retains the
pre-migration target data and restores it on failure; post-restore checks
validate health, bytes, and partitions. MCP verification checks the internal
protocol and the unauthenticated gateway response. The shared GCP endpoint
check uses the same explicit target IP as the existing Toolkit acceptance
step.

Entry point: `observability_operations.yml`. The entry point targets only
localhost; the service scripts make remote connections only to the explicit
IPs in the workflow environment. No inventory or CMDB is inferred here.

`post_dns_cutover` receives an explicit domain, target IPv4, health path and
`RESTART_CADDY=true|false`. When a restart is requested it also requires an
SSH user and runtime key path. The role restarts Caddy on the selected host and
checks HTTPS with normal certificate validation, resolving the supplied domain
to that exact target IP. The control workflow decides when to run it and invokes
the IaC DNS executor to restore its checkpoint on failure. No DNS/provider token
or record write belongs to this role.
