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
