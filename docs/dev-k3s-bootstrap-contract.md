# Dev bootstrap caller and Vault contract

This owner reuses the existing single-node K3s/Flux role. The reusable workflow
is callable only through its fixed reviewed SHA; it checks out producer source
`fe467b7462fb54870079ac2945d20b815fa6dc0d`, which contains the adapter and role.
Retain that immutable source when retargeting the stacked PR. A later source
change requires a new source pin and tests, not a mutable checkout.

The caller supplies only a current-run single-host IaC `cmdb.json` artifact name,
its SHA-256, the expected instance ID, and a full GitOps commit. It must verify
IaC run success, cloud account/instance/address identity and trusted host keys
before calling; a digest alone does not establish cloud identity. No arbitrary
playbook, target, environment, Vault path, SSH option or role template is an
input. The owner projects connection metadata into a temporary narrow CMDB and
uses the existing dynamic inventory reader. PR SIT and post-main UAT resources
remain separate namespaces inside this one non-production cluster.

## Required authorization binding (not applied by this PR)

Vault JWT role: `github-actions-playbooks-dev-k3s-bootstrap`, JWT auth mount
`jwt`, audience `vault`, max TTL 1200 seconds. When Toolkit invokes this reusable
workflow, OIDC `repository`, `ref`, and `workflow_ref` identify the **Toolkit
caller**. Bind the exact approved Toolkit repository, caller workflow/ref and
`environment=dev`, and bind `job_workflow_ref` to
`ai-workspace-infra/playbooks/.github/workflows/dev-k3s-bootstrap.yml@<reviewed-full-wrapper-SHA>`.
Do not set `repository=playbooks` for this cross-repository call. The wrapper has
no workflow_dispatch entry; a Playbooks-owned run would require a different
binding and is not silently interchangeable. Use no wildcard workflow allowlist.

Read-only KV permission is limited to `kv/data/CICD/dev/k3s-bootstrap`, fields:
`SSH_USER` (authorized system administrator), `SSH_PRIVATE_KEY` (corresponding
management identity), and `SSH_KNOWN_HOSTS` (independently verified entries for
the current IaC address). No provider, production, state or business record is
needed by this bootstrap owner. Normal JWT login and self-revocation complete
authentication lifecycle; no Vault role/policy/record is created or modified.
No GitHub Secret fallback is supported. The private key is sent to ssh-agent via
stdin, raw command output is not published, and only the runtime receipt is
uploaded. Known hosts are mandatory; there is no keyscan/accept-new bypass.

## Acceptance and rollback

The role synchronously reconciles Flux and requires an actual single Ready
control-plane node, exact OS hostname, cluster/node UIDs, current observed
resource generations, Ready source/root and exact applied Git commit. Its
receipt contains selected public version/identity fields and fixed input/run
hashes. Contract tests use synthetic objects only and are not runtime evidence.
A missing/failed receipt blocks deployment; no fixture receipt is installed.

This workflow configures the selected host and does not create, destroy, alter
cloud resources/DNS or change Terraform state ownership. Bootstrap management
access is not a business identity. Reverting the GitOps commit is a configuration
rollback; node identity or data changes require their separately scoped recovery.
