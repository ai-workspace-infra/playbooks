# Personal AI Aggregator v1 role

The GitOps `spec.gateway` selects the entry mode and adapter. New API issues user API tokens and maintains the user, plan, quota and usage ledger. CPA and LiteLLM are its upstream channels.

## APISIX mode

Select `entry_mode: gateway`, `adapter: apisix`, `mode: standalone`, `runtime_config_backend: gitops-file`, and `etcd: false`. APISIX listens on loopback port 9080. Set `auth_mode: new-api-token-pass-through` and `runtime_secret_refs: {}`.

`bootstrap_client_key` is an old APISIX Consumer credential. It cannot authenticate a New API user. The role does not fetch or write this key. Candidate New API routes must normalize and pass the original user token; gateway Key Auth, JWT Auth and direct AI provider plugins are rejected in this profile.

Generate the bundle from the gateway component's `contracts/ai-internal-new-api.yaml`, adapting the hostname to the selected environment. The bundle must have Standalone loopback configuration, disabled Admin/Control APIs and the `#END` marker. Existing Prometheus configuration is preserved.

```bash
ansible-playbook -i inventory.ini deploy_ai_aggregator.yaml \
  -e ai_aggregator_manifest_file=/absolute/path/to/ai-aggregator.yaml \
  -e ai_aggregator_apisix_bundle_dir=/absolute/path/to/rendered/apisix \
  -e ai_aggregator_operation=plan
```

`stage` publishes configurations and units, fetching New API runtime secrets from Vault into tmpfs. Binaries must already be installed and pinned. CPA OAuth is node-local, mode 0700, on operator-provided encrypted storage. Prepare CPA nodes with `deploy_ai_desktop.yml` and `ai_desktop_cpa_codeagent=true`.

Before `activate`, record `spec.apisix.activation_validated: true` after user-token, OAuth and protocol verification. CPA starts before New API and APISIX; Caddy reloads after configuration validation. Runtime secrets require reinjection after reboot.

## Direct mode

Select `entry_mode: direct-new-api`. Caddy forwards to the existing healthy New API service; New API authenticates and accounts for both CPA and LiteLLM channels. A dormant `adapter` configuration may remain for rollback.

This mode changes the Caddy route only. `stage` writes `ai-aggregator.caddy.candidate`; `activate` preserves the active fragment, publishes the candidate, validates Caddy and reloads. Failure restores the previous fragment. APISIX/Kong processes are left unchanged and receive no traffic from this route. New API and its Vault runtime material must already be provisioned.

## Kong compatibility

`entry_mode: gateway` and `adapter: kong` select the retained legacy Kong deployment tasks. Kong requires its own Traditional/PostgreSQL runtime and loopback Admin API. The public gateway renderer supports New API token pass-through, but the legacy deployment tasks still require separate migration and node-level acceptance. Contract/render tests do not establish runtime interchangeability.

## Verification

Use a New API user token for `/v1/models` and a minimum inference request, then confirm the same user's usage record in New API. OpenAI uses Bearer authentication; Anthropic's `x-api-key` and the legacy `apikey` header are normalized by the gateway. Missing/invalid tokens must be rejected. Never store user tokens or OAuth bundles in Git, unit files, CI output or artifacts.

An unauthenticated 401 proves the rejection path only. APISIX/Kong cutover requires an authorized request and a rollback check on the target host.
