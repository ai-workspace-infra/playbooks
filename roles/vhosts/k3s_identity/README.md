# Kubernetes three-role identities

Single responsibility: initialize, deliver and verify independent root/operator/
auditor Kubernetes client identities on selected existing `k3s` masters.
Does not install the cluster/controllers, create OS users, author RBAC, change
cloud resources or provision a Vault policy. Run as system administrator;
per-user authentication probes run as the corresponding OS user.

## Dependencies and inputs

Run common first to establish OS users/groups. K3s API, master-local client CA
and GitOps-managed RBAC must be ready. Runtime VAULT_ADDR/VAULT_TOKEN must
provide a short-lived writer authorized for read/create on only the three exact
KV v2 data paths; no root token or fallback identity. Preparing/applying that
controlled writer policy belongs to its separate reviewed owner operation.

| Input | Purpose |
| --- | --- |
| `k3s_identity_environment`, `k3s_identity_cluster` | Reviewed credential scope, `kv/<environment>/<cluster>/<role>` |
| `k3s_identity_api_server`, `k3s_identity_tls_dir` | Actual HTTPS endpoint and master-local CA location |
| `k3s_identity_cluster_uid` | Optional expected kube-system UID; absent is bound from the real API |
| `k3s_identity_state_dir` | Private master state, parent must exist |
| `k3s_identity_certificate_days` | Short certificate lifetime, default 7 days |
| `k3s_identity_checks` | Explicit root/operator/auditor cases, at least one allow and one deny per role |
| `k3s_platform_kubeconfig_path`, `k3s_platform_install_dir` | Admin API access and helper location |

See [defaults](defaults/main.yml) and [three-role design](docs/three-role-design.md).
The generator is owned here; `scripts/bootstrap_k3s_identity_keys.py` is the
thin manual wrapper for the same implementation, not a second issuer.

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

Deliver each `~/.kube/identity-config` with directory 0700/file 0600; do not
replace default recovery configs. Real `kubectl auth whoami` and `can-i` must
match caller-defined allow/deny cases and mandatory non-admin secret/exec/
attach/proxy/token/bind/escalate/impersonation denials. Auditor write denials
are mandatory. A config/Flux receipt is not real-identity acceptance.

This refactor preserves these probes; RBAC readiness/writer access and live
three-identity acceptance still require target-cluster evidence. It introduces
no automatic certificate revocation or rollback: deleting KV records does not
revoke certificates, and reverting code does not restore credentials or data.
