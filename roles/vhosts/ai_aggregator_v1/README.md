# Personal AI Aggregator v1 role

This role reads the selected GitOps `PersonalAIAggregator` declaration.
`plan` validates topology and prints the roles assigned to each target. `stage`
creates non-secret directories, units and Caddy fragments, but deliberately
does not enable or start a service. `activate` is a separate, explicit
operation: the playbook starts CPA groups first, then LiteLLM and New API, and
reloads Caddy only after `caddy validate` succeeds.

The artifact installer and Vault-authentication synchronizer are separate
implementation gates: they require pinned upstream release assets, a verified
New API loopback bind mechanism, and a node identity with least-privilege Vault
policies. Do not start the rendered units until those gates are complete.

CPA endpoints are not taken from the `cmdb://` placeholder in GitOps at
runtime. The gateway resolves each target node's `private_ip` from generated or
existing inventory, then writes the resulting channel map to `/run/ai-aggregator`
tmpfs. Missing private CMDB data blocks gateway staging.
