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
| `k3s_platform_flux_root_name` | `platform-root` | Explicit root name for generated bootstrap and runtime verification. |
| `k3s_platform_flux_bootstrap_mode` | `generated` | `generated` preserves existing callers; `declarative_paused` stages the unified GitOps graph without reconcile. |
| `k3s_platform_gitops_checkout_path` | empty | Clean controller-side GitOps checkout at `k3s_platform_git_commit` for paused mode. |

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
cleanup require a separate, explicitly scoped recovery action. Reverting an artifact
does not reverse schema or data changes; stateful generation upgrades require a new
cluster and independent storage under the applicable upgrade policy.

## Unified paused declaration bootstrap

Use `declarative_paused` for an initial staged generation. The controller requires
Python with PyYAML and `kubectl kustomize`, a clean checkout at the exact full
`k3s_platform_git_commit`, and fetched stable tags used by its source declarations.
The declared root directory must include its own root Flux Kustomization, bootstrap
GitRepository, independent per-unit GitRepositories, namespaces and child Flux
Kustomizations. Each child path owns its chart sources and suspended HelmReleases.

Before host changes, the helper builds and validates the root and all child paths.
All initial Git source pins must resolve to the reviewed bootstrap commit; all
GitRepositories, Flux Kustomizations and HelmReleases must be explicitly suspended.
It rejects branch refs, missing/dirty checkouts, sources shared across child units,
path escapes, unknown resources, conflicting duplicates and unsuspended releases.
Each unit can use its own chart/digest pin. After bootstrap, its Git configuration
pin and reconciliation state can advance separately through GitOps review.

The role loads those exact objects and skips generated root/source creation and
both reconcile commands. It refuses existing active Flux resources and never
marks paused resources Ready. The Dev Ready-receipt option is incompatible with
this mode. Normal release updates, including independently advancing unit tags,
do not rerun initial bootstrap; update the authoritative GitOps graph instead.
Do not apply an old paused checkout over an active generation. Authentication
remains runtime-managed; the bundle accepts no Kubernetes Secrets or Jobs.

## Immutable source and runtime receipt

Set `k3s_platform_git_commit` to a full 40-character commit SHA to render
`GitRepository.spec.ref.commit`; a nonempty malformed SHA fails before changes.
The existing branch setting remains supported for existing callers. Reconciliation
now runs synchronously. The Dev wrapper explicitly enables
`k3s_platform_strict_dev_receipt` (default false); existing generic callers do
not inherit the single-node constraint. When enabled, the bounded verifier
requires a single expected Ready
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

### Fresh generation data mount and paused receipt

`k3s_platform_require_data_mount` requires the UUID verified by
`web_saas_data_volume` at `/data`; `k3s_platform_local_storage_path` routes the
local provisioner to `/data/k3s/storage`. A systemd mount dependency prevents
K3s starting against an unmounted system-disk directory. The V6 owner adapter
consumes one receipt-verified CMDB, fixed GitOps checkout and provider-attested
SSH keys. No V5 inventory or DNS operation is involved.

The separate `prod-k3s-bootstrap` action loads the reviewed graph paused and
emits `PAUSED_BOOTSTRAP_VERIFIED`, including actual node/cluster UID, version,
mount UUID and resource counts. It does not assert business readiness. An active
release graph is rejected. On failure retain the new node and disk for diagnosis;
do not reformat or downgrade an existing stateful release. Before activating
workloads review credentials, RBAC, application UID, storage and network access.

The system-admin-only `scripts/prod_k3s_identity_setup.py` initializes the two
reviewed V6 Vault roles and dedicated SSH binding from immutable source files.
It does not run in CI or fall back to an administrator token. Secrets remain in
memory and Vault; existing conflicting roles/keys are not overwritten.

Fresh data-mounted clusters create locked Unix `cluster-audit` and a separate
status-only ServiceAccount. They receive no sudo/runtime group, admin kubeconfig,
Secrets, pod execution, proxy or write permissions. Live tests use the Unix user
and a bounded in-memory ServiceAccount token to prove status reads and denied
secret/proxy/writes (namespace write probe is server-side dry run). Business
identities remain separate and must be qualified before releases are resumed.

### V6 ESO and TLS prerequisites

For `prod-k3s-v6`, the paused bundle includes the ESO HelmRelease and all nine
Flux Kustomizations. The `vault-tls-identity` and `vault-tls-bootstrap` paths
are rendered and their exact object identities checked, but their ServiceAccount,
RBAC, SecretStore and ExternalSecret are deliberately not applied by initial
bootstrap. Their own paused Flux Kustomizations control when they are applied;
ESO CRDs may not yet be installed. The business units retain distinct sources.

This validation describes a fresh paused bootstrap, not an authorization to rerun
it on an already initialized cluster or to replace its live Flux graph. A separate
reviewed transition must reconcile an existing graph while preserving its state.
Identity-object readiness does not prove Vault allow/deny authorization; that is a
separate evidence gate before certificate synchronization.
