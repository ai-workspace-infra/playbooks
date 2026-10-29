# V6 workload identity adapter

The V6 adapter validates its reviewed GitOps checkout, CMDB, bootstrap receipt,
instance and provider-attested SSH host key. It supplies the V6-specific values
to the generic `vhosts/vault_jwt_workload_identity` implementation; identity
configuration and authorization checks have a single owner implementation.

A reviewed caller must authenticate the artifact provenance, provide a short-lived
scoped administrator token for configuration, and independently validate the
result before releasing certificate synchronization. Failure during creation can
leave partial Vault identity state; inspect it before any retry. This adapter does
not deploy services or alter DNS.
