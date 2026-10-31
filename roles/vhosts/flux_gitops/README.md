# Flux GitOps handover

Single responsibility: hand a reviewed GitOps source/root to existing Flux
controllers, verify handover, and write a public receipt. Target: `k3s`; run as
the system administrator with an initialized API, ready Flux controllers,
private admin kubeconfig and host Python/PyYAML. It does not install K3s/Flux,
issue Kubernetes identities, apply business workloads or change DNS.

## Inputs and phases

| Input | Contract |
| --- | --- |
| `k3s_platform_git_url` | Required repository URL; no production fallback |
| `k3s_platform_git_commit`, `k3s_platform_git_branch` | Resolved commit, or explicit branch in generated mode; branch defaults to main |
| `k3s_platform_flux_namespace`, `k3s_platform_flux_source_name`, `k3s_platform_flux_root_name` | Independent object identities; source/root names required, not inferred from node |
| `k3s_platform_flux_root_path` | Required GitOps path, independent of hostname |
| `k3s_platform_flux_bootstrap_mode` | `generated` or `declarative_active`; no paused handover |
| `k3s_platform_gitops_checkout_path` | Clean controller checkout at the resolved full SHA in declarative mode |
| `k3s_platform_git_auth_mode` | public, ssh, https-basic or https-bearer; runtime auth only |
| `k3s_platform_receipt_path` | Public controller receipt destination, required in active mode |
| `k3s_platform_strict_dev_receipt` | Optional stricter single-node generated-mode observer; false by default |

Full inputs and runtime credential lookups: [defaults](defaults/main.yml).
`tasks/preflight.yml` is the read-only preparation phase: validate contract,
render declared graph on the controller and resolve runtime authentication.
The coordinator calls it before host changes. `tasks/main.yml` validates the
same contract for independent reuse, then runs handover with `always` cleanup
of temporary credentials/bundle, including failures. Runtime secret values
remain suppressed; this is not credential provisioning or policy expansion.

```yaml
- hosts: k3s
  become: true
  roles:
    - vhosts/flux_gitops
```

## Output and retries

In declarative mode, the role applies only the selected source/root; Flux owns
all child declarations and services. The existing active verifier checks node,
independent disk, cluster UID, readiness and the actual source/applied SHA and
emits `FLUX_HANDOVER_VERIFIED`. Common evidence is attached to the caller's
receipt. Generated mode supports the optional bounded readiness receipt.

Readiness/acceptance of RBAC, Vault, databases and applications is separate.
Do not make source/root readiness depend on the credentials that are issued
only after handover. GitOps must structure its RBAC dependencies accordingly.
Retry against the same reviewed revision; changing source/path is a reviewed
release action. Reverting code does not restore data, revoke certificates,
remove Flux resources or change DNS.
