# Agent Proxy SecretID issuer (prototype)

This prototype accepts HTTPS client certificates, requires a separately managed
SHA-256 certificate fingerprint allowlist, logs into Vault with a projected
Kubernetes service account token and returns a response-wrapped SecretID. It is
not connected to a deployment or an Ansible role and must not be deployed as-is.

Before rollout, supply and review certificate enrollment, renewal and
revocation, issuer Vault policy and role, network exposure, rate limiting,
health checks, token lifetime, node service ordering and end-to-end tests. No
client certificate private key or Vault administrative token belongs in Git.
