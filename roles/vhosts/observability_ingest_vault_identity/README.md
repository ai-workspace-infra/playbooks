# Observability ingestion Vault identity

Reconciles a dedicated Kubernetes-auth role and ACL for the authenticated
remote-write receiver from reviewed GitOps declarations. It creates missing
resources, refuses differing existing resources, and checks Vault readback.
The Kubernetes auth mount must already exist. The caller supplies the reviewed
GitOps checkout, Vault HTTPS address, and a short-lived administrative token.
Set `OBSERVABILITY_INGEST_VAULT_MOUNT` to the reviewed Kubernetes auth mount.
Default mode checks only; `observability_ingest_vault_identity_apply: true`
creates missing resources. The caller must verify the actual service-account
allow/deny behavior and preserve Vault audit evidence. Revoke the role and policy
only after the ingestion consumer has been removed or safely switched.
