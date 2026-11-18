# XConnect Edge Agent Role

Single-purpose L3 Control Plane role managing the `/usr/local/bin/agent-proxy` binary, `/etc/agent/account-agent.yaml`, `/etc/agent/conf.d/*.yaml` multi-profile configs, and `xconnect-edge-agent.service`.

## Scope & Boundaries

- **Owns**:
  - Release installation/migration for `/usr/local/bin/agent-proxy`
  - `/etc/agent/account-agent.yaml` (base settings or legacy single-file config)
  - `/etc/agent/conf.d/NN-*.yaml` (split profiles for `agent-proxy`, `gateway`, or declarative `xconnect_edge_agent_profiles`)
  - `/etc/systemd/system/xconnect-edge-agent.service` and legacy service retirement (`agent-proxy`, `agent-svc-plus`)
  - Initial registration probe to `{{ agent_controller_url }}/api/agent-server/v1/status`
- **Does NOT own**:
  - Ingress engines (`caddy`, `fluxgate`, `haproxy`) or vhost fragments (`conf.d/*.conf`)
  - Xray core binary (`xray-core`), Xray templates (`xconnect_proxy`), or Gateway WireGuard (`xconnect_gateway`)
  - Prometheus exporters (`xray-exporter`) or Vector (`vector-agent`)

Compose those roles in the playbook's `roles:` list instead of nesting `include_role` inside this role.
