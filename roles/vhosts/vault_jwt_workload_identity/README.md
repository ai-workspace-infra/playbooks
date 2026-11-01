# Vault JWT workload identity

This reusable role creates a dedicated Vault JWT authentication mount and
single-path read policy and verifies a workload's allowed and denied access. It accepts explicit target and identity
parameters and has no cluster-generation, namespace, audience, Vault endpoint or
Kubernetes version defaults. The caller must validate CMDB, node identity,
host-key attestation and receipt provenance.

`vault_jwt_workload_identity_mode` is `configure`, `inspect`, `refresh`, or `verify` (default `verify`).

The
`vault_jwt_workload_identity_config` mapping supplies `host`, `ssh_user`, `known_hosts`,
`cluster_uid`, `node_uid`, `node_name`, `expected_version`, `vault_address`,
`admin_policy` for configure, and `identity`: mount, role, policy, namespace,
service_account, audience, denied_audience, probe_service_account, allowed_path,
denied_path and optional ttl_seconds. `vault_jwt_workload_identity_runtime`
selects `k3s` or `kubernetes`; default is `k3s`. Pass an isolated SSH agent and,
for configure, inspect, or refresh, a short-lived token with exactly the named admin policy.

The role verifies the live cluster/node identifiers before obtaining short-lived
TokenRequests, binds issuer and published signing keys, and accepts existing
identity resources only when their type and reviewed fields match exactly.
Missing resources may be created after all existing state has been inspected;
unreviewed values stop the operation before writes. Verify mode confirms the
exact record is readable and that the denied path, wrong audience and wrong
service account are refused. Secrets and token values stay in memory; task
output is suppressed and the result fact contains only redacted status and
cluster IDs.

The caller owns access, attestation, revocation and partial failure handling. Flux reconciles GitOps independently.

Configuration is resumable after partial failure: a retry reads the dedicated
mount, reader policy, auth config and role first; it creates only missing items
and leaves exact matching items untouched. Any type or content mismatch blocks
before writes. The admin token therefore requires read on only this mount's
config and role plus its existing exact mount and policy scope.

The `inspect` mode reads the current JWT config and compares its validation-key
fingerprints with the authenticated cluster's live JWKS. It emits SHA-256
fingerprints and match booleans only; PEM keys, tokens, and credentials are not
included in output. Use these fingerprints for independent review before refresh.
The owner command accepts the separately reviewed fingerprint list for `refresh`;
refresh rejects an absent or malformed list and verifies the live old-key set
matches it before the write.

The `refresh` role mode is an explicit signing-key replacement for a verified
existing cluster. Its `expected_previous_key_sha256` input is the reviewed list
of SHA-256 fingerprints of the current PEM keys. It validates the JWT mount,
issuer, exact reader policy and role before replacing keys with the live cluster
keys, reads back the result, and performs allow/deny checks. Vault JWT config
has no compare-and-swap: callers must serialize access to the mount and review
the old fingerprint and target identity. A failure after writing is a partial
change, not a successful receipt; inspect state before retry.
