# xworkmate_bridge

This document records the current real deployment and runtime validation state for the XWorkmate ACP bridge stack.

## Scope

`roles/vhosts/xworkmate_bridge` owns the public ingress and validation contract for `xworkmate-bridge.svc.plus`.

The provider runtimes remain separate sibling roles:

- [`roles/vhosts/acp_codex`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_codex)
- [`roles/vhosts/acp_opencode`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_opencode)
- [`roles/vhosts/acp_gemini`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_gemini)
- [`roles/vhosts/acp_server_hermes`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/acp_server_hermes)

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
- Codex ACP bridge: `acp-codex.service`
- OpenCode ACP bridge: `acp-opencode.service`
- Gemini ACP adapter: `acp-gemini.service`
- Hermes ACP adapter: `acp-hermes.service`

Behavior after deployment:

- requests without `Authorization: Bearer $INTERNAL_SERVICE_TOKEN` are rejected
- requests with `Authorization: Bearer $INTERNAL_SERVICE_TOKEN` are accepted
- this playbook only defines and validates the shared ingress token path
- provider-specific authentication and ACP method compatibility are intentionally left to the individual runtimes
- the Codex runtime user is a role variable and defaults to `ubuntu`, so it can be changed from inventory if needed
- Gemini adapter is now also aligned to `ubuntu` home paths so it can reuse `/home/ubuntu/.gemini/oauth_creds.json`

## Public Endpoints

App-facing endpoints:

- `wss://xworkmate-bridge.svc.plus/acp`: canonical WebSocket JSON-RPC runtime
- `https://xworkmate-bridge.svc.plus/acp/rpc`: HTTP JSON-RPC fallback for capabilities, routing, agent, multi-agent, cancel, close, CI, and diagnostics
- `https://xworkmate-bridge.svc.plus/gateway/openclaw`: dedicated OpenClaw task submit endpoint for `session.start` and follow-up `session.message`
- `https://xworkmate-bridge.svc.plus/api/ping`: release and runtime health probe

Non-contract routes:

- Provider-direct routes such as `/codex`, `/opencode`, `/gemini`, `/hermes`, and legacy ACP provider paths are not public APP contracts
- `/gateway/openclaw` is not a global ACP base endpoint and must not be used for capabilities, routing, cancel, or close

## Post-Deploy Verification

Without token:

- `https://xworkmate-bridge.svc.plus/acp/rpc` -> `401`
- `https://xworkmate-bridge.svc.plus/gateway/openclaw` -> `401`

With `Authorization: Bearer $INTERNAL_SERVICE_TOKEN`:

- `wss://xworkmate-bridge.svc.plus/acp` -> `101 Switching Protocols`
- `https://xworkmate-bridge.svc.plus/acp/rpc` `acp.capabilities` -> `200`
- `https://xworkmate-bridge.svc.plus/acp/rpc` `xworkmate.routing.resolve` -> `200`
- `https://xworkmate-bridge.svc.plus/gateway/openclaw` `session.start` -> `200` with either success or structured provider failure

Bridge public root:

- `https://xworkmate-bridge.svc.plus/` -> `200`

Expected body:

- `xworkmate-bridge is running`

## JSON-RPC AI Chat Task Validation

A real JSON-RPC chat task was executed with the prompt:

```text
请只回复：AI chat task ok
```

Agent tasks use `/acp/rpc` or `/acp` with explicit routing metadata:

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

OpenClaw task submission uses the same JSON-RPC envelope at `/gateway/openclaw`, with `routing.explicitExecutionTarget=gateway` and `routing.preferredGatewayProviderId=openclaw`. Follow-up `session.message` for the same OpenClaw task also stays on `/gateway/openclaw`.

## Current Operational Status

- ACP ingress deployment: validates `/api*`, `/acp*`, `/gateway/openclaw`, and `/`
- unified internal bearer token: required for protected endpoints
- bridge public root route: working
- legacy ACP provider Caddy fragments: removed from public ingress

## Out Of Scope

The following issues may still need attention at the provider/runtime layer, but they are not defined as playbook responsibilities:

1. Remote Codex/OpenAI runtime authentication configuration used by `codex-app-server`
2. Gemini adapter protocol mapping for executable chat methods such as bridge `session.start` and `session.message`
