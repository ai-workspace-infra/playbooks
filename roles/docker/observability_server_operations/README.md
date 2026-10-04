# Observability service operations

This role owns the telemetry store migration and service acceptance logic used
by the Toolkit's Observability control workflow. It runs on the GitHub Actions
control runner and connects to the explicit UAT source/target hosts with the
runtime SSH key supplied by Vault. The Toolkit remains responsible for UAT
eligibility, operator confirmations, job ordering, and DNS cutover approval.

The role accepts `observability_operation` values `data_migrate`,
`verify_store`, `verify_target`, `verify_mcp`, `post_dns_cutover`, or
`verify_local_grafana`, or `xconnect_remote_observation`, and requires
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

`verify_local_grafana` is an independent read-only operation: pass an explicit
inventory and `observability_local_health_host` naming one existing host. The
controller delegates a bounded HTTP probe to that host's loopback Grafana port
(default 3030), requiring HTTP 200 and a JSON `database: ok` response. It does not
run the deployment role, restart services, follow redirects, use a proxy or fall
back to another target. Response bodies are hidden; only sanitized host/status
evidence is printed. The Toolkit records the pinned owner SHA and decides whether
that evidence permits the next stage. This verifies local Grafana only, not TLS,
telemetry history, public routing or overall UAT acceptance.

```sh
ansible-playbook -i "$ACCESS_DIR/inventory.ini" observability_operations.yml \
  -e observability_operations_environment=uat \
  -e observability_operation=verify_local_grafana \
  -e "observability_local_health_host=$NODE_NAME"
```

`xconnect_runtime_contract` is a separate read-only operation for one explicit
Gateway or One host. It validates the XHTTP/Xray JSON contract previously
checked by the Toolkit's remote helper: One must have the local UDP
`dokodemo-door` and the expected VLESS/XHTTP/TLS outbound; Gateway must have
either the direct TLS or managed Caddy Unix-socket inbound plus the local
WireGuard freedom outbound. The role reads only the supplied paths, never
refreshes configuration, restarts Xray/Caddy, or mutates state. The operation is
UAT-only and prints only a stable summary.

Example:

```sh
ansible-playbook -i "$ACCESS_DIR/inventory.ini" observability_operations.yml \
  -e observability_operations_environment=uat \
  -e observability_operation=xconnect_runtime_contract \
  -e xconnect_runtime_contract_delegate_host=xconnect-gateway \
  -e xconnect_runtime_contract_role=gateway \
  -e xconnect_runtime_contract_path=/var/lib/xconnect-gateway/runtime/xray.json \
  -e xconnect_runtime_contract_remote_address=tw-xconnect.svc.plus \
  -e xconnect_runtime_contract_server_name=tw-xconnect.svc.plus \
  -e xconnect_runtime_contract_xhttp_path=/xconnect \
  -e xconnect_runtime_contract_xhttp_mode=auto \
  -e xconnect_runtime_contract_xhttp_host=tw-xconnect.svc.plus
```

`post_dns_cutover` receives an explicit domain, target IPv4, health path and
`RESTART_CADDY=true|false`. When a restart is requested it also requires an
SSH user and runtime key path. The role restarts Caddy on the selected host and
checks HTTPS with normal certificate validation, resolving the supplied domain
to that exact target IP. The control workflow decides when to run it and invokes
the IaC DNS executor to restore its checkpoint on failure. No DNS/provider token
or record write belongs to this role.

`xconnect_remote_observation` is a separate read-only operation for an
explicit Gateway host and One host. It validates the signed state binding,
runtime status, and bounded WireGuard handshakes, then emits only the stable
`NODE_OBSERVATION` summary contract. The One probe reads `xconnect status`
without refreshing the runtime. State directories, device/network identifiers,
interfaces, and the public peer key are required inputs; no host or target is
inferred. Raw command output is hidden and the operation never prints
credentials, signed configuration, or peer material.

Example:

```sh
ansible-playbook -i "$ACCESS_DIR/inventory.ini" observability_operations.yml \
  -e observability_operations_environment=uat \
  -e observability_operation=xconnect_remote_observation \
  -e xconnect_remote_observation_gateway_host=xconnect-gateway \
  -e xconnect_remote_observation_client_host=xconnect-one
```
