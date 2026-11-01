# Agent Proxy Vector Vault credentials

This work-in-progress role configures a separate Vault Agent to authenticate
with a node-specific TLS client certificate and render the configured KV v2
credentials for Vector. The existing TLS certificate agent is independent.

Required inputs include `VAULT_ADDR`, `AGENT_PROXY_VECTOR_VAULT_KV_PATH`,
`VAULT_TLS_SECRET_PATH`, `AGENT_PROXY_CERT_VAULT_PATH`,
`agent_proxy_vector_vault_auth_cert_mount`, `agent_proxy_vector_vault_auth_cert_role`,
and the client certificate and private-key file paths. The files must be
provisioned securely with root ownership and mode 0600. Vault must trust the
client certificate and grant only the configured KV path and token lifecycle
permissions. The endpoint must preserve or securely forward the validated
client certificate through any TLS-terminating reverse proxy.

The observability data path and the Caddy TLS data path are read-only runtime
inputs. `AGENT_PROXY_CERT_VAULT_PATH` is a separate per-node KV v2 API data
path used only by the bootstrap token to write the client identity; for example
`kv/data/CICD/agent-proxy/<node>/client-identity`. It must differ from both
read paths. The runtime certificate role is not granted write access to it.

Do not deploy this role yet: runtime-directory recreation, certificate
issuance and renewal, proxy forwarding, and full end-to-end validation remain
unfinished. Rollback is to restore the prior Vector configuration and stop
the dedicated monitoring Vault Agent without changing the TLS agent.
