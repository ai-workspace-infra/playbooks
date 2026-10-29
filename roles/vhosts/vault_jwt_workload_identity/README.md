# Vault JWT workload identity

This reusable role creates a dedicated Vault JWT authentication mount and
single-path read policy, verifies a workload's allowed and denied access, or
releases one reviewed V6 Flux stage. It accepts explicit target and identity
parameters and has no cluster-generation, namespace, audience, Vault endpoint or
Kubernetes version defaults. The caller must validate CMDB, node identity,
host-key attestation and receipt provenance.

`vault_jwt_workload_identity_mode` is `configure`, `verify`, `release-root`,
`release-eso`, or `release-identity` (default `verify`). Release modes require
the pinned GitOps checkout and immutable tag. They apply only the declared V6
management source and root, then wait for the exact gate set in that release.
The root stage keeps every child paused; the ESO stage enables only ESO; the
identity stage additionally enables the Vault TLS reader ServiceAccount gate.
Later stages are intentionally not inferred or enabled by these modes.

The
`vault_jwt_workload_identity_config` mapping supplies `host`, `ssh_user`, `known_hosts`,
`cluster_uid`, `node_uid`, `node_name`, `expected_version`, `vault_address`,
`admin_policy` for configure, and `identity`: mount, role, policy, namespace,
service_account, audience, denied_audience, probe_service_account, allowed_path,
denied_path and optional ttl_seconds. `vault_jwt_workload_identity_runtime`
selects `k3s` or `kubernetes`; default is `k3s`. Pass an isolated SSH agent and,
for configure only, a short-lived token with exactly the named admin policy.

The role verifies the live cluster/node identifiers before obtaining short-lived
TokenRequests, binds issuer and published signing keys, and accepts existing
identity resources only when their type and reviewed fields match exactly.
Missing resources may be created after all existing state has been inspected;
unreviewed values stop the operation before writes. Verify mode confirms the
exact record is readable and that the denied path, wrong audience and wrong
service account are refused. Secrets and token values stay in memory; task
output is suppressed and the result fact contains only redacted status and
cluster IDs.

The caller owns staged rollout, access to the authenticated Vault endpoint,
artifact and key attestation, revocation/rollback, and interpretation of a
partial configure failure. Flux release modes do not change DNS, database
state, business writers, or service HelmReleases outside the explicitly allowed
V6 gate set.

Configuration is resumable after partial failure: a retry reads the dedicated
mount, reader policy, auth config and role first; it creates only missing items
and leaves exact matching items untouched. Any type or content mismatch blocks
before writes. The admin token therefore requires read on only this mount's
config and role plus its existing exact mount and policy scope.
