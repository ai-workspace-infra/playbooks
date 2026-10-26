# K3s platform bootstrap

Bootstraps a single-node K3s control plane and connects Flux to a GitOps root.

## Inputs

| Variable | Default | Purpose |
|---|---|---|
| `k3s_platform_k3s_version` | `v1.37.1+k3s1` | Pinned K3s version used for initial installation. |
| `k3s_platform_helm_version` | `v3.22.1` | Pinned Helm CLI version installed by the role. |
| `k3s_platform_flux_version` | `2.9.6` | Pinned Flux CLI version installed by the role. |
| `k3s_platform_node_name` | `inventory_hostname` | Stable Kubernetes node identity. |
| `k3s_platform_retired_hostnames` | `[]` | Explicit old names to remove from `/etc/hosts` during a host identity change. |
| `k3s_platform_write_kubeconfig_mode` | `0644` | Kubeconfig mode; use `0600` for a single administrator. |
| `k3s_platform_git_url` | Environment override or repository default | GitOps source URL. |
| `k3s_platform_git_branch` | `main` | GitOps branch to reconcile. |
| `k3s_platform_flux_root_path` | Environment override or production root | Kustomization root in GitOps. |

The role runs as a host administrator on the explicitly selected `k3s` group. It
does not create cloud resources or DNS records. Secret Git credentials must be
provided at runtime and are not committed with this role. Temporary bootstrap
scripts, environment files, keys, and installer logs are removed after Flux
resources are verified.

## Example

Use an IaC-generated CMDB inventory for normal delivery. A one-time manual
inventory is limited to a specifically authorized operation and must be sourced
from current cloud control-plane facts, protected with mode `0600`, and removed
after execution.

```yaml
k3s_platform_node_name: dev-k3s-v5
k3s_platform_write_kubeconfig_mode: "0600"
k3s_platform_git_url: https://github.com/ai-workspace-infra/gitops.git
k3s_platform_git_branch: main
k3s_platform_flux_root_path: ./environments/clusters/dev-k3s-v5
k3s_platform_git_auth_mode: public
```

## Rollback

This role does not remove K3s or its data directory. Reverting the GitOps
source/path returns Flux to the previous declared root. K3s removal and data
cleanup require a separate, explicitly scoped recovery action.

## Immutable source and runtime receipt

Set `k3s_platform_git_commit` to a full 40-character commit SHA to render
`GitRepository.spec.ref.commit`; a nonempty malformed SHA fails before changes.
The existing branch setting remains supported for existing callers. Reconciliation
now runs synchronously and the bounded verifier requires a single expected Ready
control-plane node, a real kube-system UID, current observed generations,
GitRepository and root Kustomization Ready, and matching source/applied revisions.
With a commit input, both source spec and observed artifact must match that SHA.
`k3s_platform_ready_timeout` bounds observation (default 600 seconds, max 1200).
`k3s_platform_receipt_path` optionally writes only the verified public receipt on
the controller. Node hostname/UID, cluster UID, versions and actual revision are
observations; a missing receipt is a failed bootstrap, never a fixture success.

The Dev reusable adapter consumes a single current-run CMDB artifact and digest,
requires the expected instance ID, narrows inventory connection variables, and
loads only its fixed non-production SSH binding from Vault via caller OIDC.
Trusted known_hosts are mandatory; private keys are loaded via stdin into an
isolated ssh-agent and never written to a key file. This role still runs as a
system administrator for bootstrap; that is not a business or audit identity.
