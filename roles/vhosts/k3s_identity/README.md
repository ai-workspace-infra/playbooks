# Kubernetes three-role identities

Single responsibility: initialize, deliver and verify independent root/operator/
auditor Kubernetes client identities on selected existing `k3s` masters.
Does not install the cluster/controllers, create OS users, author RBAC, change
cloud resources or provision a Vault policy. Run as system administrator;
per-user authentication probes run as the corresponding OS user.

## Dependencies and inputs

Run common first to establish OS users/groups. K3s API, master-local client CA
and GitOps-managed RBAC must be ready. Runtime `VAULT_ADDR` and
`K3S_IDENTITY_VAULT_TOKEN` must
provide a short-lived writer authorized for read/create on only the three exact
KV v2 data paths; no root token or fallback identity. Preparing/applying that
controlled writer policy belongs to its separate reviewed owner operation.
The controller must obtain the writer separately from the common OS reader;
the identity role rejects a missing writer or a token equal to `VAULT_TOKEN`.
Token inequality alone does not prove its policy scope: the caller must verify
the actual role and allow/deny behavior. The writer must remain valid until
credential generation; an expired token fails closed.

| Input | Purpose |
| --- | --- |
| `k3s_identity_environment`, `k3s_identity_cluster` | Reviewed credential scope, `kv/<environment>/<cluster>/<role>` |
| `k3s_identity_api_server`, `k3s_identity_tls_dir` | Actual HTTPS endpoint and master-local CA location |
| `k3s_identity_cluster_uid` | Optional expected kube-system UID; absent is bound from the real API |
| `k3s_identity_state_dir` | Private master state, parent must exist |
| `k3s_identity_certificate_days` | Short certificate lifetime, default 7 days |
| `k3s_identity_checks` | Explicit root/operator/auditor cases, at least one allow and one deny per role |
| `k3s_identity_rbac_kustomizations` | Non-empty list of `{name, namespace}` Flux Kustomizations that deliver the reviewed RBAC |
| `k3s_identity_rbac_revision` | Full GitOps commit SHA; defaults to the current bootstrap GitOps commit |
| `k3s_platform_kubeconfig_path`, `k3s_platform_install_dir` | Admin API access and helper location |

See [defaults](defaults/main.yml) and [three-role design](docs/three-role-design.md).
The generator is owned here; `scripts/bootstrap_k3s_identity_keys.py` is the
thin manual wrapper for the same implementation, not a second issuer.

After Flux handover and before credential issuance, the role waits for each
declared RBAC Kustomization to be Ready at its current observed generation and
to report the reviewed commit as its last applied revision. Missing objects,
stale generations, unhealthy status and old commits fail after the bounded
retry period. This confirms reconciliation, not effective permissions: the
real-identity allow/deny probes remain mandatory. The RBAC source must resolve
to that same commit; use an explicit reviewed revision for a standalone run.

```bash
ansible-playbook -i inventory/terraform_cmdb.py k3s_identity_bootstrap.yml \
  --limit '<selected-master>' -e @/secure/path/reviewed-identity-vars.yml
```

## Output, idempotency and rollback

The master generates unique keys/certificates for all three subjects, verifies
chain/key/subject/expiration, and creates private kubeconfigs. No system:masters
group is issued. CA private keys stay on the master. KV v2 CAS=0 and read-back
prevent accidental overwrite; protected local state permits bounded retries.
Conflicting target/cluster/trust, partial state or expired material fails closed;
there is no automatic rotation or deletion to force retry.

Sanitized failure exit codes are 21 for preflight, 22 for local issuance and
23 for Vault archival. These indicate only the failed stage; they do not expose
credential values or identify a specific root cause.

Deliver each `~/.kube/identity-config` with directory 0700/file 0600; do not
replace existing unmanaged configs. For OS `operator` and `auditor`, the role safely
links `~/.kube/config` to that user's own `identity-config` after rejecting any
pre-existing unmanaged path, then verifies default `kubectl auth whoami` still
identifies that same user outside `system:masters`. This makes ordinary `kubectl`
use the already-scoped GitOps RBAC without granting OS sudo, access to the
K3s admin kubeconfig, or container runtime socket permissions. Root
continues using the explicit `identity-config` path. Real `kubectl auth whoami` and `can-i` must
match caller-defined allow/deny cases and mandatory non-admin secret/exec/
attach/proxy/token/bind/escalate/impersonation denials. Auditor write denials
are mandatory. A config/Flux receipt is not real-identity acceptance.

This refactor preserves these probes; RBAC readiness/writer access and live
three-identity acceptance still require target-cluster evidence. It introduces
no automatic certificate revocation or rollback: deleting KV records does not
revoke certificates, and reverting code does not restore credentials or data.
