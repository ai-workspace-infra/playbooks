# Personal AI Aggregator v1 role

## Default: APISIX Standalone

`deploy_ai_aggregator.yaml` defaults to APISIX. Declare `spec.gateway.adapter:
apisix`, `mode: standalone`, `runtime_config_backend: gitops-file`, `etcd: false`.
Set `spec.apisix.bind_address: 127.0.0.1`, `proxy_port: 9080`, and provide
`runtime_secret_refs` mapping environment variable names to environment-scoped
Vault references. Set `ai_aggregator_apisix_bundle_dir` to the controller-side
directory produced by the public gateway renderer. Its bundle must include
`ai-proxy-multi`, the selected entrypoint host, disabled Admin/Control APIs,
loopback binding and the `#END` marker. Consumer credentials use runtime references.

```bash
ansible-playbook -i inventory.ini deploy_ai_aggregator.yaml \
  -e ai_aggregator_manifest_file=/absolute/path/to/ai-aggregator.yaml \
  -e ai_aggregator_apisix_bundle_dir=/absolute/path/to/rendered/apisix \
  -e ai_aggregator_operation=plan
```

Use `stage` to publish units/configuration and fetch Vault values into tmpfs.
APISIX/OpenResty, New API and CPA binaries must already be installed and pinned.
New API runtime credentials are injected separately from APISIX provider values.
CPA OAuth directories are node-local, mode 0700, on operator-provided encrypted
storage. CPA nodes are prepared separately with `deploy_ai_desktop.yml` and
`ai_desktop_cpa_codeagent=true`. This role does not implement disk encryption.

Before `activate`, record `spec.apisix.activation_validated: true` after manual
OAuth, consumer authentication and protocol checks. Activation runs CPA hosts
first, then New API and APISIX, validates Caddy and reloads HTTPS last. APISIX uses
systemd, no etcd or Docker. Backed-up configuration files support manual rollback;
provider environment changes require an APISIX restart. Runtime injection must be
repeated after reboot because `/run` is volatile. Real-node testing remains required.

## Legacy Kong compatibility

Explicit `spec.gateway.adapter: kong` selects the retained legacy implementation.

This role reads the selected GitOps `PersonalAIAggregator` declaration.
`plan` validates topology and prints the roles assigned to each target. `stage`
creates non-secret directories, units and Caddy fragments, but deliberately
does not enable or start a service. `activate` is a separate, explicit
operation: the playbook starts CPA groups first, then LiteLLM and New API, then
Kong, and reloads Caddy only after `caddy validate` succeeds.

Caddy is a thin HTTPS edge: it automatically manages certificates and forwards
both configured hosts to the Kong proxy listener. Kong owns Host/Path routing,
tenant authentication, ACL, rate limits, and audit metadata. New API and
LiteLLM are not direct Caddy upstreams. Automatic certificate issuance depends
on the environment's ACME validation path being reachable for both hostnames.

The artifact installer and Vault-authentication synchronizer are separate
implementation gates: they require pinned upstream release assets, a verified
New API loopback bind mechanism, and a node identity with least-privilege Vault
policies. Do not start the rendered units until those gates are complete.

CPA endpoints are not taken from the `cmdb://` placeholder in GitOps at
runtime. The gateway resolves each target node's `private_ip` from generated or
existing inventory, then writes the resulting channel map to `/run/ai-aggregator`
tmpfs. Missing private CMDB data blocks gateway staging.
