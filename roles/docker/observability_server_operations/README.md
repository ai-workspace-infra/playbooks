# Observability service operations

This role owns the telemetry store migration and service acceptance logic used
by the Toolkit's Observability control workflow. It runs on the GitHub Actions
control runner and connects to the explicit UAT source/target hosts with the
runtime SSH key supplied by Vault. The Toolkit remains responsible for UAT
eligibility, operator confirmations, job ordering, and DNS cutover approval.

The role accepts `observability_operation` values `data_migrate`,
`verify_store`, `verify_target`, or `verify_mcp`, and requires
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
