# Personal AI Aggregator v1 role

## IaC handoff deployment entry

The composite action `.github/actions/ai-aggregator-deploy` executes this role
for one host. The calling job must select its protected GitHub Environment,
checkout this owner at a reviewed SHA, and download the current run's IaC
summary artifact by the artifact ID returned by `iac-orchestrator.yml`.
Expected checksums, commits and correlation IDs come from trusted job outputs,
never from the downloaded payload. This entry does not download or authenticate
GitHub artifacts itself.

Pass `config-json` with these required fields:

| Fields | Meaning |
| --- | --- |
| `operation` | `plan`, `stage` or `activate` |
| `environment`, `hostname`, `domain` | Exact selected GitOps environment and service target |
| `gitops_root`, `gitops_sha`, `service_manifest` | Clean pinned GitOps checkout and repository-relative service declaration |
| `iac_root`, `iac_sha`, `target_manifest`, `correlation_id` | Clean pinned IaC owner checkout and original target selection |
| `summary_file`, `summary_sha256` | Current-run IaC summary and trusted expected checksum; sibling `inventory.json` is required |
| `cmdb_file` | CMDB produced by IaC `iac-caller-inventory`; all facts must equal the verified inventory |
| `ssh_private_key`, `known_hosts` | Prepared runner-private key and independently trusted SSH host keys |
| `apisix_bundle_dir` | Required for `stage`; validated APISIX configuration bundle |

The job needs Python with PyYAML/Jinja2 and `ansible-playbook` installed.
`stage` consumes the runtime `VAULT_TOKEN` through the existing role; secrets
must not be included in `config-json`, summaries, CMDB or receipt files.
Pass `receipt-file` as an output path and upload it with failure on missing
files. A successful receipt says `playbook_completed`; it does not assert
application readiness, inference acceptance or DNS cutover.

The entry validates the full summary with the selected IaC owner, binds it to
the current run and attempt, and checks the exact GitOps host/domain and CMDB
before executing Ansible with strict SSH host-key checking. It creates no
cloud resources, static inventory or CMDB. Both a newly created Akamai host
and an explicitly selected existing Akamai host must arrive through the same
verified IaC apply evidence. This entry does not import unmanaged resources or
accept arbitrary host IP overrides.

Use `plan` first. Retry the same pinned inputs after resolving a failure;
runtime identity changes require a new IaC receipt. The current role requires
preinstalled pinned binaries and valid runtime prerequisites, as described
below. Rollback and OAuth/data migrations remain separate explicit operations.

The GitOps `spec.gateway` selects the entry mode and adapter. New API issues user API tokens and maintains the user, plan, quota and usage ledger. CPA and LiteLLM are its upstream channels.

`spec.new_api.public_models` is the single public model allowlist. Optional
`spec.litellm.models` entries describe non-sensitive LiteLLM modules and are
rendered into the LiteLLM config; their provider credentials are resolved from
the matching `spec.litellm.provider_secret_refs` Vault records. For example,
the NVIDIA module uses `litellm/providers/nvidia` and `NVIDIA_API_KEY` at
runtime. A provider discovery catalog is never promoted automatically.

## APISIX mode

Select `entry_mode: gateway`, `adapter: apisix`, `mode: standalone`, `runtime_config_backend: gitops-file`, and `etcd: false`. APISIX listens on loopback port 9080. Set `auth_mode: new-api-token-pass-through` and `runtime_secret_refs: {}`.

`bootstrap_client_key` is an old APISIX Consumer credential. It cannot authenticate a New API user. The role does not fetch or write this key. Candidate New API routes must normalize and pass the original user token; gateway Key Auth, JWT Auth and direct AI provider plugins are rejected in this profile.

Generate the bundle from the gateway component's `contracts/ai-internal-new-api.yaml`, adapting the hostname to the selected environment. The bundle must have Standalone loopback configuration, disabled Admin/Control APIs and the `#END` marker. Existing Prometheus configuration is preserved.

```bash
ansible-playbook -i inventory/terraform_cmdb.py deploy_ai_aggregator.yaml \
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
