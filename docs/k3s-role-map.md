# K3S Role Map

This document defines the recommended reuse path for `playbooks/roles/vhosts/k3s*`.

## Goal

The `k3s*` roles are no longer treated as one flat group.

They are split into:

- baseline roles used by all nodes
- platform bootstrap roles used by GitOps-driven single-node platform deployments
- cluster lifecycle roles used by multi-node cluster deployments
- frozen legacy roles kept only for compatibility
- reset roles kept for teardown and recovery

## Recommended Mainline

Recommended path for a new platform project:

`common -> k3s_platform_bootstrap -> k3s_platform_addon -> GitOps`

Responsibilities:

- `common`
  - host baseline
  - package baseline
  - file limits
  - shared OS hardening
- `k3s_platform_bootstrap`
  - install single-node k3s
  - render `/etc/rancher/k3s/config.yaml`
  - disable built-in components such as `traefik`
  - install Flux
  - connect the node to the GitOps repository
- `k3s_platform_addon`
  - install platform-side shared components into Kubernetes
  - examples: `external-secrets`, `reloader`, `caddy`, `apisix`, `external-dns`
- `GitOps`
  - own dynamic platform configuration
  - own workload manifests
  - own service wiring and dependencies

Entry playbooks:

- `playbooks/k3s_platform_bootstrap_with_gitops.yml`
- `playbooks/k3s_platform_addon.yml`

Use this path when:

- the target is a standard platform node
- Flux/GitOps is the desired source of truth
- platform addons should be declared instead of installed by ad hoc scripts

## Cluster Mainline

Recommended path for a new multi-node cluster project:

`common -> k3s-cluster-server / k3s-cluster-agent`

Responsibilities:

- `common`
  - shared host baseline before cluster work starts
- `k3s-cluster-server`
  - server lifecycle role
  - dispatches by `action`
  - current action set includes `bootstrap`, `add-master`, `backup`, `recovery`, `upgrade`, `destroy`
- `k3s-cluster-agent`
  - agent lifecycle role
  - dispatches by `action`
  - current action set includes `bootstrap`, `upgrade`, `destroy`

Entry playbooks:

- `playbooks/init_k3s_cluster_server`
- `playbooks/init_k3s_cluster_agent`

Use this path when:

- the target is a server/agent topology
- cluster lifecycle needs to be managed explicitly
- node roles and actions are different between control plane and workers

## Role Status

| Role | Status | Use |
| --- | --- | --- |
| `vhosts/k3s_platform_bootstrap` | recommended | single-node platform bootstrap |
| `vhosts/k3s_platform_addon` | recommended | platform addon installation before GitOps takeover |
| `vhosts/k3s-cluster-server` | recommended | cluster server lifecycle |
| `vhosts/k3s-cluster-agent` | recommended | cluster agent lifecycle |
| `vhosts/k3s-reset` | keep | reset / teardown |
| `vhosts/k3s` | frozen | legacy single-node installer |
| `vhosts/k3s-cluster` | frozen | legacy script-driven cluster installer |
| `vhosts/k3s-addon` | frozen | legacy ingress and DNS addon wrapper |

## Frozen Role Migration

The frozen roles are not the recommended extension points anymore.

### `vhosts/k3s`

Legacy behavior:

- runs `setup-k3s.sh`
- optionally installs `kubeovn`

Replace with:

- `common`
- `k3s_platform_bootstrap`

Notes:

- `k3s_platform_bootstrap` is the right place to absorb the installation capability from `vhosts/k3s`
- the target direction is capability fusion followed by gradual retirement of the old `vhosts/k3s` role
- the preferred reuse mode is capability extraction and inward migration, not direct role chaining
- shared logic worth absorbing includes installer download, mirror selection, default k3s flags, and helm bootstrap
- `kubeovn` and legacy CNI-specific behavior should not be pulled into the new platform bootstrap path by default
- new single-node installs should be expressed through rendered k3s config and Flux bootstrap
- do not add new platform capabilities to `vhosts/k3s`

### `vhosts/k3s-cluster`

Legacy behavior:

- copies shell scripts to remote hosts
- runs `setup_k3s.sh`
- runs `set-registry.sh`

Replace with:

- `common`
- `k3s-cluster-server`
- `k3s-cluster-agent`

Notes:

- new lifecycle actions should be added to the server or agent action roles
- do not extend the script-wrapper model further

### `vhosts/k3s-addon`

Legacy behavior:

- wraps ingress and DNS shell scripts
- mixes ingress installation details with addon orchestration

Replace with:

- `k3s_platform_addon`
- GitOps-managed service configuration

Notes:

- new ingress, gateway, DNS, and platform addon logic should move into `k3s_platform_addon`
- keep `k3s-addon` only for compatibility with older environments

## New Project Selection

For a new project, choose the path by deployment style.

### Option A: Platform Node With GitOps

Choose:

`common -> k3s_platform_bootstrap -> k3s_platform_addon -> GitOps`

Best for:

- `svc.plus` style platform nodes
- single-node or platform-first installations
- shared addons managed centrally
- GitOps as the long-term control plane

### Option B: Multi-Node Cluster Lifecycle

Choose:

`common -> k3s-cluster-server / k3s-cluster-agent`

Best for:

- explicit server/agent topologies
- controlled bootstrap, upgrade, backup, and recovery flows
- clusters that need role-aware lifecycle operations

### Option C: Reset Only

Choose:

`k3s-reset`

Best for:

- teardown
- rebuild preparation
- cleanup after failed bootstrap

## Extension Rules

When adding new capabilities:

- host-level baseline belongs in `common`
- k3s installation and Flux bootstrap belong in `k3s_platform_bootstrap`
- platform shared addons belong in `k3s_platform_addon`
- server and agent lifecycle actions belong in `k3s-cluster-server` or `k3s-cluster-agent`
- dynamic service configuration belongs in GitOps
- reset and cleanup behavior belongs in `k3s-reset`

Do not add new functionality to:

- `vhosts/k3s`
- `vhosts/k3s-cluster`
- `vhosts/k3s-addon`

## Reuse Guidance

`vhosts/k3s` is frozen as an external entry role, but its useful install logic should be migrated inward.

Preferred direction:

- keep `vhosts/k3s` frozen as a compatibility wrapper
- move reusable k3s install logic into `k3s_platform_bootstrap` over time
- let `k3s_platform_bootstrap` become the single canonical owner of the platform install path
- shrink `vhosts/k3s` as pieces are absorbed until it can be removed safely
- treat `k3s_platform_bootstrap` as the canonical owner of the new single-node platform bootstrap path

Avoid:

- adding `include_role: vhosts/k3s` inside `k3s_platform_bootstrap`
- extending the old script-wrapper interface as the long-term contract

## Short Decision Rule

If the question is "which role should a new project reuse?", use:

- platform project: `common -> k3s_platform_bootstrap -> k3s_platform_addon -> GitOps`
- multi-node cluster project: `common -> k3s-cluster-server / k3s-cluster-agent`
- cleanup project: `k3s-reset`
