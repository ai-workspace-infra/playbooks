# K3s platform bootstrap

Bootstraps a single-node K3s control plane and connects Flux to a GitOps root.

## Target initialization scope

The platform bootstrap covers node/cluster initialization, coordination of the
OS and Kubernetes `root`, `operator`, and `auditor` identities, Flux controllers,
External Secrets Operator (ESO), and the Ingress controller. GitOps declares
the desired components and versions; the role establishes the cluster and Flux,
loads the reviewed declarations, and verifies each component as its gate is
released. `vhosts/common` remains responsible for OS preparation.

| Order | Initialization and gate |
| --- | --- |
| 1 | Verify the IaC/CMDB target and independent storage; run `vhosts/common` to prepare and validate the three OS accounts, then initialize K3s and verify the node and API. |
| 2 | Install Flux controllers and load the fixed GitOps source and root declarations. |
| 3 | Generate independent credentials and private kubeconfigs on the initialized master, verify their trust chain, and write them to Vault; reconcile the reviewed GitOps RBAC, deliver each user's kubeconfig through the K3s role, and validate with all three actual identities. |
| 4 | Release and verify ESO and its CRDs; establish the reviewed external Vault identity and secret synchronization prerequisites. |
| 5 | Initialize and validate the Ingress controller through its own GitOps release. For a generation replacement, do this after Vault, PostgreSQL, observability, and business deployment; validate without changing DNS. |

This is the intended scope, not a claim that each stage is implemented or active.
The current role directly installs K3s and Flux and stages the paused GitOps graph;
ESO and Ingress are independently reconciled from GitOps after their respective
gates are released. Three-role credentials and RBAC delivery remain unfinished.
Ingress controller initialization does not itself authorize a DNS cutover.
OS accounts and SSH remain owned by `vhosts/common`; the K3s role coordinates
their separately authenticated Kubernetes identities, while GitOps owns RBAC
and Vault stores the per-cluster credentials. See the
[three-role design](docs/three-role-design.md) for the contract and acceptance.

The [OS and Kubernetes three-role design](docs/three-role-design.md) describes
the proposed generic root/operator/auditor identities, GitOps declarations,
execution boundaries, migration/audit reads and acceptance. It is a design,
not evidence that these Kubernetes identities have been installed.

## Identity initialization contract

K3s master initialization owns the three-identity lifecycle. After verifying the
OS accounts, cluster API and target identity, the role generates independent
client private keys on the master, signs short-lived client certificates through
the cluster's controlled administrator signing path, verifies the client trust
chain and key pairing, and generates each user's private kubeconfig. It writes
the generated credential records to `kv/<environment>/<cluster>/<role>` using a
narrowly scoped runtime Vault identity. The signing CA private key stays on the
master and is never included in those records or handed to the other users.

Credentials are generated during initialization, not supplied as three prebuilt
JSON files. `bootstrap_k3s_identity_keys.py` must be adapted to this node-side
flow. Its current file-import implementation and the existing read-only delivery
tasks are incomplete draft code and do not implement this contract yet.
Normal reruns must verify and reuse matching valid credentials; rotation and
replacement require an explicit reviewed mode rather than unconditional overwrite.
GitOps owns RBAC; real authentication and allow/deny acceptance follow generation
and reconciliation. No production command is provided for the incomplete draft.

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

## Independent identity delivery: implementation in progress

`tasks/identity_delivery.yml` is an explicit tasks entrypoint, not yet included in
normal bootstrap. It requires `k3s_identity_environment`, `k3s_identity_cluster`,
`k3s_identity_cluster_uid`, `k3s_identity_api_server`, `k3s_identity_ca_data`
(base64 CA), and `k3s_identity_checks` keyed by root/operator/auditor. Each check
has verb, resource, namespace and boolean allowed. The controller reads exact
KV v2 paths using runtime VAULT_ADDR/VAULT_TOKEN; secret tasks suppress logs.
This draft currently expects existing credentials; it must be replaced by the master-side generation flow described above. Existing OS accounts remain a prerequisite.
It writes each user's `.kube/identity-config`, verifies `kubectl auth whoami`,
and evaluates authorization cases using real credentials. GitOps owns RBAC.

This entrypoint has only YAML parsing validation so far. Mandatory security
case validation, certificate-chain validation, GitOps reconciliation evidence,
revocation/rollback and isolated integration tests remain required before use.
Do not call this entrypoint against production yet. It does not install RBAC
or remove existing admin or legacy audit credentials.
