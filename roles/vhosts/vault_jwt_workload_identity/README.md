# Vault JWT workload identity

This reusable role creates a dedicated Vault JWT authentication mount and
single-path read policy or verifies a workload's allowed and denied access. It
accepts explicit target and identity parameters and has no cluster-generation,
namespace, audience, Vault endpoint or Kubernetes version defaults. The caller
must validate CMDB, node identity, host-key attestation and receipt provenance.

`vault_jwt_workload_identity_mode` is `configure` or `verify` (default). The
`vault_jwt_workload_identity_config` mapping supplies `host`, `ssh_user`, `known_hosts`,
`cluster_uid`, `node_uid`, `node_name`, `expected_version`, `vault_address`,
`admin_policy` for configure, and `identity`: mount, role, policy, namespace,
service_account, audience, denied_audience, probe_service_account, allowed_path,
denied_path and optional ttl_seconds. `vault_jwt_workload_identity_runtime`
selects `k3s` or `kubernetes`; default is `k3s`. Pass an isolated SSH agent and,
for configure only, a short-lived token with exactly the named admin policy.

The role verifies the live cluster/node identifiers before obtaining short-lived
TokenRequests, binds issuer and published signing keys, and rejects existing
mounts or policies instead of overwriting them. Verify mode confirms the exact
record is readable and that the denied path, wrong audience and wrong service
account are refused. Secrets and token values stay in memory; task output is
suppressed and the result fact contains only redacted status and cluster IDs.

The caller owns staged rollout, access to the authenticated Vault endpoint,
artifact and key attestation, revocation/rollback, and interpretation of a
partial configure failure. This role changes no DNS, database, or Flux release.
