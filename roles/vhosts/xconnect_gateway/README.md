# Zero-driven XConnect Gateway

`xconnect_gateway_environment` accepts `uat`, `prod`, or `shared`. Use the
`shared` value for a shared-services Gateway; its invitation and credentials
must still be supplied at runtime from a dedicated shared Vault path.

This is the canonical OS-level Gateway role. It installs reviewed Gateway and
external Xray artifacts, initializes protected local state, exchanges a
short-lived Zero invitation, reconciles signed Gateway configuration, starts
the WireGuard/Xray data plane, and keeps it synchronized through a systemd
timer.

Together with `vhosts/xconnect_one`, this forms the two Zero-driven roles:
Gateway is the relay/service role and One is the controlled-client role.

The role does not create cloud resources, VPCs, security groups, DNS records,
or Portal/Accounts data. Binary sources, TLS material, trust bundles, and
invitations are runtime inputs; GitOps remains limited to non-sensitive
topology and release selection.

`xconnect_gateway_trust_bundle_source` is the Gateway-owned public trust
material (for example Vault `kv/data/CICD/domains/svc.plus` field
`tls_trust_bundle_pem_b64` decoded by the caller). The role installs it at
`/etc/xconnect-gateway/ca.crt`. Set `xconnect_gateway_ca_fetch_dest` when the
controller must fetch that public CA for a subsequent `vhosts/xconnect_one`
deployment. Only the public CA is fetched; the Gateway TLS private key never
leaves the Gateway/Vault boundary. One must consume this handoff and must not
generate its own CA.

## Shared Caddy frontend

For a host that also runs Agent Proxy, set:

```yaml
xconnect_gateway_frontend: caddy-unix-h2c
xconnect_gateway_listen_socket: /run/xconnect-gateway/xray.sock
xconnect_gateway_socket_group: caddy
```

The Gateway Xray then uses its own runtime configuration and Unix socket. It
does not bind TCP `443` and does not reuse Agent Proxy's
`/usr/local/etc/xray/config.json` or `/dev/shm/xray.sock`. Configure the
`vhosts/tky-proxy` role with `xconnect_gateway_caddy_enabled: true` to add the
`/xconnect` route beside Agent Proxy's existing `/split` route. Caddy remains
the only TLS listener on TCP `443`.

The Accounts signed Gateway transport must carry the same non-sensitive
frontend contract (`frontend: caddy-unix-h2c` and `listen_socket`). The VLESS
UUID remains runtime-signed and is never placed in Caddy or GitOps.
