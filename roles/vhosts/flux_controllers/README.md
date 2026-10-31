# Flux controllers

Single responsibility: install the Flux client/controllers and verify controller
rollouts on selected `k3s` hosts. Requires an initialized, ready Kubernetes API,
a private admin kubeconfig and host tooling (curl/CA trust); does not install
K3s, Helm, ESO, application charts or GitRepository/Kustomization entrypoints.
Run as the system administrator.

Inputs: `k3s_platform_flux_version` (pinned CLI release),
`k3s_platform_flux_namespace` (controller namespace), and
`k3s_platform_kubeconfig_path` (admin configuration). See
[defaults](defaults/main.yml). These shared input names match the platform
caller; namespaces remain overridable non-secret GitOps inputs.

```yaml
- hosts: k3s
  become: true
  roles:
    - vhosts/flux_controllers
```

Completion requires source, kustomize, helm and notification controller rollout
success. Repeated installation converges the controller manifests; an existing
CLI is preserved. This does not prove GitOps handover or service readiness.
Do not remove controller CRDs to roll back. Controller release changes and
restoring a prior declared version require a reviewed operation; this role
provides no automatic downgrade or resource deletion.
