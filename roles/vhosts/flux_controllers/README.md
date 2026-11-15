# Flux controllers

Single responsibility: install the Flux client/controllers, verify controller
rollouts, and enforce the declared controller resource bounds on selected `k3s`
hosts. Requires an initialized, ready Kubernetes API, a private admin kubeconfig
and host tooling (curl/CA trust); does not install K3s, Helm, ESO, application
charts or GitRepository/Kustomization entrypoints. Run as the system
administrator.

Inputs: `k3s_platform_flux_version` (pinned CLI release),
`k3s_platform_flux_namespace` (controller namespace),
`k3s_platform_kubeconfig_path` (admin configuration) and
`k3s_platform_flux_controller_resources` (per-controller requests/limits). See
[defaults](defaults/main.yml). These shared input names match the platform
caller; namespaces remain overridable non-secret GitOps inputs. Memory limits
default to 256-512Mi so a small k3s node keeps headroom for workloads.

```yaml
- hosts: k3s
  become: true
  roles:
    - vhosts/flux_controllers
```

Completion requires source, kustomize, helm and notification controller rollout
success, plus a read-back check that every controller reports the declared
memory limit. The resource-bounds phase lives in
[tasks/limits.yml](tasks/limits.yml) and is tagged `flux_limits` /
`configure` / `validate`, so it can also run on its own.

## Pull-mode convergence

`pull_flux_controller_limits.yml` at the repository root applies only the limits
phase, so a target host can converge the bounds without re-running the bootstrap:

```bash
sudo ansible-pull -U https://github.com/ai-workspace-infra/playbooks.git \
  -d /var/lib/ansible-pull/playbooks -i localhost, \
  pull_flux_controller_limits.yml
```

The `-i localhost,` override is required because the repository default
inventory is the IaC-generated CMDB.

To change the bounds, edit `k3s_platform_flux_controller_resources` in
[defaults](defaults/main.yml) and re-run either the role or the pull entrypoint.

Repeated installation converges the controller manifests; an existing CLI is
preserved. This does not prove GitOps handover or service readiness. Do not
remove controller CRDs to roll back. Controller release changes and restoring a
prior declared version require a reviewed operation; this role provides no
automatic downgrade or resource deletion. Rolling back a resource-bound change
means restoring the previous `k3s_platform_flux_controller_resources` values and
re-running the limits phase.
