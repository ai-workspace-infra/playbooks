# xworkmate_bridge

This document records the current real deployment and runtime validation state for the XWorkmate ACP bridge stack.

## Scope

There is no standalone Ansible role implementation under `roles/vhosts/xworkmate_bridge` yet.

The active implementation currently lives in:

- [`roles/vhosts/deploy_acp_vhosts`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/deploy_acp_vhosts)
- [`roles/vhosts/acp_codex`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_codex)
- [`roles/vhosts/acp_opencode`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_opencode)
- [`roles/vhosts/acp_gemini`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_gemini)

This README is the umbrella operations note for that deployed stack.

## Real Deployment

The stack was redeployed with a real run, not dry-run, using the committed inventory from `HEAD`.

Command pattern:

```bash
tmp_inventory=$(mktemp)
git show HEAD:inventory.ini > "$tmp_inventory"
ansible-playbook -i "$tmp_inventory" deploy_xworkmate_bridge_vhosts.yml -l jp-xhttp-contabo.svc.plus
rm -f "$tmp_inventory"
```

Latest verified real deployment result:

- play recap: `ok=91 changed=17 failed=0 unreachable=0`

## Token Source

`INTERNAL_SERVICE_TOKEN` was loaded from:

- [`accounts.svc.plus/.env`](/Users/shenlan/workspaces/cloud-neutral-toolkit/accounts.svc.plus/.env)

Deployment and verification used that token value from `.env` instead of requiring the shell session to be pre-exported.

## Unified Authentication

The ACP entrypoints now reuse the same `INTERNAL_SERVICE_TOKEN`.

Applied areas:

- main bridge service: `xworkmate-bridge.service`
- Codex ACP bridge: `acp-bridge-codex.service`
- OpenCode ACP bridge: `acp-bridge-opencode.service`
- Gemini ACP adapter: `acp-gemini-adapter.service`

Behavior after deployment:

- requests without `Authorization: Bearer $INTERNAL_SERVICE_TOKEN` are rejected
- requests with `Authorization: Bearer $INTERNAL_SERVICE_TOKEN` are accepted
- this playbook only defines and validates the shared ingress token path
- provider-specific authentication and ACP method compatibility are intentionally left to the individual runtimes

## Public Endpoints

Base domains:

- `https://xworkmate-bridge.svc.plus/`
- `https://acp-server.svc.plus/codex`
- `https://acp-server.svc.plus/opencode`
- `https://acp-server.svc.plus/gemini`

Correct ACP RPC endpoints:

- Codex HTTP RPC: `https://acp-server.svc.plus/codex/acp/rpc`
- Codex WebSocket: `wss://acp-server.svc.plus/codex/acp`
- OpenCode HTTP RPC: `https://acp-server.svc.plus/opencode/acp/rpc`
- OpenCode WebSocket: `wss://acp-server.svc.plus/opencode/acp`
- Gemini HTTP RPC: `https://acp-server.svc.plus/gemini/acp/rpc`
- Gemini WebSocket: `wss://acp-server.svc.plus/gemini/acp`

Note:

- `https://acp-server.svc.plus/gemini` is not the RPC endpoint and should not be used as the ACP health check target.

## Post-Deploy Verification

Without token:

- `https://acp-server.svc.plus/codex/acp` -> `401`

With `Authorization: Bearer $INTERNAL_SERVICE_TOKEN`:

- `https://acp-server.svc.plus/codex/acp/rpc` -> `200`
- `https://acp-server.svc.plus/opencode/acp/rpc` -> `200`
- `https://acp-server.svc.plus/gemini/acp/rpc` -> `200`

Bridge public root:

- `https://xworkmate-bridge.svc.plus/` -> `200`

Expected body:

- `xworkmate-bridge is running`

## JSON-RPC AI Chat Task Validation

A real JSON-RPC chat task was executed with the prompt:

```text
请只回复：AI chat task ok
```

The request needed explicit routing metadata for Codex and OpenCode:

```json
{
  "jsonrpc": "2.0",
  "id": "example",
  "method": "session.start",
  "params": {
    "sessionId": "example",
    "threadId": "example",
    "taskPrompt": "请只回复：AI chat task ok",
    "workingDirectory": "/root",
    "routing": {
      "routingMode": "explicit",
      "explicitExecutionTarget": "singleAgent",
      "explicitProviderId": "codex"
    }
  }
}
```

Observed results:

- OpenCode: success
  - output: `AI chat task ok`
- Codex: request reached the bridge, but upstream Codex execution failed
  - observed upstream failures included repeated `500` on `wss://api.openai.com/v1/responses`
  - final upstream error also included `401 Unauthorized` for missing OpenAI bearer/basic authentication
  - conclusion: bridge auth is correct, but the remote Codex/OpenAI runtime authentication configuration is outside this playbook's scope
- Gemini: ACP auth is correct, but upstream protocol is not yet fully compatible
  - response: `"Method not found": session.start`
  - conclusion: Gemini executable chat flow is blocked by upstream ACP method compatibility and adapter-side protocol mapping gaps, which are outside this playbook's scope

## Current Operational Status

- ACP ingress deployment: working
- unified internal bearer token: working
- bridge public root route: working
- OpenCode ACP JSON-RPC chat execution: working
- Codex ACP JSON-RPC chat execution: bridge path working; upstream runtime authentication is outside this playbook's scope
- Gemini ACP JSON-RPC chat execution: bridge path working; upstream method compatibility is outside this playbook's scope

## Out Of Scope

The following issues may still need attention at the provider/runtime layer, but they are not defined as playbook responsibilities:

1. Remote Codex/OpenAI runtime authentication configuration used by `codex-app-server`
2. Gemini adapter protocol mapping for executable chat methods such as bridge `session.start` and `session.message`
