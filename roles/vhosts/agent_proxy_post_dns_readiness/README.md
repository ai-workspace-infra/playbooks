# Agent Proxy post-DNS readiness

This role runs against one selected Agent Proxy host after DNS reconciliation.
It requires non-empty Xray runtime configuration, starts `xray.service` and
`xray-tcp.service` through Ansible's systemd module, then checks the complete
service set with a bounded wait. On failure it prints sanitized service
diagnostics.

Optional variables:

- `agent_proxy_post_dns_ready_timeout_seconds` (default `180`)
- `agent_proxy_post_dns_ready_poll_seconds` (default `5`)
- `agent_proxy_runtime_config_files` (default: the two Xray JSON configs)

Entrypoint: `verify_agent_proxy_post_dns.yml`. Set `readiness_target_host` to
exactly one inventory host and use `--limit` for that host. Starting Xray after
DNS is a deliberate convergence action inherited from the Toolkit workflow.
