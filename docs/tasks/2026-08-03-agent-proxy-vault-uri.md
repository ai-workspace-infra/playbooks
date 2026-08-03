# 2026-08-03 Agent Proxy Vault UUID URI path

## Root cause

The Agent Proxy Gate still failed in the delegated Vault UUID task after the
runner and interpreter fixes because `community.hashi_vault` continued to
load `hvac` from the controller Python environment.

## Minimal fix

The agent-proxy playbook now uses Ansible's built-in `uri` module for the two
Vault KV v2 operations required by the Xray UUID:

- `GET /v1/kv/data/<env>/agent-proxy` to read the existing UUID;
- `POST /v1/kv/data/<env>/agent-proxy` to persist a generated UUID.

The Vault token remains supplied at runtime, no secret is written to disk, and
only the UAT agent-proxy UUID path changes. This removes the `hvac` dependency
from the failing gate while preserving the same KV v2 data shape.

## Verification

- `git diff --check`
- `ansible-playbook --syntax-check -i cmdb/inventory.ini deploy_xray_proxy_server.yml`
- Follow-up UAT run with r6, 2C4G, `web-saas + agent-proxy`, and UAT DNS update
  enabled.
