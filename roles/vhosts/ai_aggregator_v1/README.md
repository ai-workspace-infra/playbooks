# Personal AI Aggregator v1 role

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
