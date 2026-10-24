# K3s Cluster Server Bootstrap

This role prepares a K3s server, installs the Flux and ESO controllers, and connects the cluster to the declared GitOps root. It does not provision DNS records or configure Caddy routes; those remain declared in GitOps and rendered by the owning Helm chart.

## GitOps inputs

| Variable | Purpose |
| --- | --- |
| `k8s_deployment_environment` | Environment selector; defaults to `uat`. |
| `k8s_gitops_ref_kind` | Git ref kind, `branch` or immutable `tag`; defaults to `branch`. |
| `k8s_gitops_ref_name` | Branch or tag name; defaults to `main`. |
| `k8s_gitops_source_name` | Flux GitRepository name; defaults to `platform-config`. |
| `k8s_gitops_kustomization_name` | Flux Kustomization name; defaults to the UAT bootstrap name. |
| `k8s_gitops_path` | Repository path reconciled by Flux; defaults to the UAT cluster root. |

For PROD, pass the reviewed GitOps release tag, `platform-config-prod`, and the matching `./environments/clusters/prod-*` path explicitly. The role fails closed when PROD is paired with a branch, a non-PROD path, or a different Flux source name. The exact release tag is selected by the deployment caller so it cannot silently drift to `main`.

Example PROD inputs:

```yaml
k8s_deployment_environment: prod
k8s_gitops_ref_kind: tag
k8s_gitops_ref_name: v<reviewed-release-tag>
k8s_gitops_source_name: platform-config-prod
k8s_gitops_kustomization_name: prod-web-saas-k3s-v5
k8s_gitops_path: ./environments/clusters/prod-web-saas-k3s-v5
```

## Scope and rollback

The role installs or reconciles K3s platform components and creates the Flux source/Kustomization resources. It does not change public DNS, host routes, application data, or existing VPS resources. To roll back the GitOps selection, re-run the role with the previously reviewed immutable tag and the matching cluster path; application rollback remains owned by Flux and the chart release.
