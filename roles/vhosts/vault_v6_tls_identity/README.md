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
