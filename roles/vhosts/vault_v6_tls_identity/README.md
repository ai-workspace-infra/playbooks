# V6 Vault TLS workload identity owner (in progress)

This owner will configure and verify the dedicated Vault JWT authentication path
used for the V6 certificate reader. `files/signing_keys.py` is the first, offline
part of that owner: it converts Kubernetes discovery/JWKS documents into an exact
issuer and RSA public-key set for Vault, rejecting unsupported or private keys.
It retains all published keys to permit service-account signing-key rotation.

The caller must fetch discovery and JWKS over an authenticated connection to the
expected K3s cluster and verify cluster identity against the attested bootstrap
receipt. Arbitrary operator-provided files are not trusted evidence. The issuer
must be bound exactly in Vault, and the initial key set must be verified using a
short-lived token from the intended service account before any service gate opens.

No Ansible tasks or live execution entrypoint are provided yet. This code does
not configure Vault, obtain tokens, sync certificates or alter cluster state. The
remaining executor must fail closed on existing divergent mounts/roles, compare
reviewed declarations, prove permitted and denied access using the actual workload
identity, emit only redacted evidence, and remain separate from DNS and migration.
Removal of this unused helper has no runtime effect; no production rollback is
required until a reviewed caller exists.

`files/vault_contract.py` contains the narrowly scoped Vault operations and
allow/deny acceptance logic. Provisioning rejects an existing dedicated mount or
policy before writing; a partial failure requires operator inspection and is not
silently retried. It validates the workload token has only the dedicated policy,
reads the allowed record without parsing its body, and requires denial for an
adjacent record and two real wrong-claim TokenRequest probes. Probe claims are
checked before login; JWT signature verification is performed by Vault.

The module is intentionally not an entrypoint. A controlled caller must acquire
three real short-lived TokenRequest JWTs from the authenticated target cluster,
verify the node and cluster UID against a trusted receipt, bind the fetched issuer
and signing keys, use an approved short-lived administrative identity, and emit a
redacted receipt. Unit tests of the contract do not establish live Vault access.

`files/cluster_identity.py` is a separate in-memory collector. It checks the
cluster UID, the unique expected node UID/name/version and node readiness against
inputs from the verified bootstrap receipt before reading API discovery or creating
three short-lived TokenRequests. It then returns the issuer, public key set and
JWTs to an in-process caller. Kubectl error output and returned tokens must not be
logged. The caller must still establish the authenticated private connection and
verify receipt provenance; locally supplied UIDs alone do not establish trust.

`files/identity_execution.py` composes receipt structure/checksum validation,
cluster collection and Vault operations. Its receipt validation is necessary but
not sufficient: an orchestration caller must retrieve the artifact from the exact
successful protected run and establish that the checksum is trusted, rather than
accepting both data and digest from an arbitrary dispatch input. It exposes no
standalone CLI or credential source. The original V6 paused-bootstrap receipt is
compatible with this contract; no live identity operation is implied.

`files/remote_kubectl.py` adapts the collector to the node through SSH without
opening the Kubernetes API publicly or copying kubeconfig to the runner. It only
permits the nine fixed discovery, identity and TokenRequest commands, enforces
strict host-key checking, disables forwarding and captures output without logging.
The authenticated SSH agent, exact CMDB-derived node address and independently
attested `known_hosts` file must be supplied by a reviewed caller. Vault admin
credentials are used by the controller process and are not sent to the node.
This adapter remains unconnected to an execution entrypoint.

`scripts/prod_k3s_vault_tls_identity.py` is the candidate owner entrypoint.
It requires the reviewed GitOps checkout, checksum-bound CMDB, bootstrap receipt,
attested host keys and an isolated SSH agent from the caller. It reuses the
existing V6 CMDB/declaration validation and matches the instance ID to the
bootstrap receipt. Child commands inherit a limited environment and do not
receive Vault or SSH private-key variables. Configure mode accepts only the
named short-lived non-root administrator policy; verify mode uses workload JWTs.
The receipt is exclusively created with mode 0600 before any Vault mutation and
contains only redacted evidence. An empty receipt means failure.

The entrypoint cannot establish the provenance of caller-supplied checksums or
host keys by itself. A reviewed orchestrator must retrieve and validate the exact
Actions artifacts and provide the SSH agent and short-lived Vault identity. It
must enforce the separate configure and allow/deny verification phases; this
owner does not release Flux or change DNS.
