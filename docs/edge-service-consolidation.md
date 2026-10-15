# Edge service consolidation

Regional edge nodes keep one systemd unit per failure domain. XHTTP and TCP
Xray transports remain separate services because they have independent config,
listener, certificate, and restart failure domains. Their exporter services
remain separate for the same reason. The consolidation is limited to the
host-level node/process metrics pair, whose ports and collectors are unchanged.

The node and process metrics exporters are installed by separate reusable roles,
then consolidated to `xconnect-host-metrics.service` when both binaries are
present. It keeps ports `9100` and `9256` and preserves the `nobody` and
`process_exporter` accounts. A node-only deployment keeps the original
`node-exporter.service` so the reusable node role remains safe on hosts that do
not install process metrics.

The resulting regional edge set is:

```text
xconnect-apisix.service             APISIX standalone data plane
xconnect-apisix-config.service      PG JSONB API + synchronizer supervisor
caddy.service                       TLS and public entry point
xray.service                        XHTTP Xray
xray-tcp.service                    TCP Xray
xray-exporter-xhttp.service         XHTTP exporter
xray-exporter-tcp.service           TCP exporter
xconnect-edge-agent.service         controller sync and user scope delivery
xconnect-host-metrics.service       node-exporter + process-exporter children
vault-agent-tls.service             Vault certificate renewal
vector.service                      logs and metric shipping
```

Only the old split host-metrics unit files are stopped, disabled, and removed
when both metrics binaries are present. APISIX remains two services because
its config API/synchronizer must be restarted independently from the data
plane; Caddy, Vault Agent, Vector, and both Xray transports remain independent
because they have different state and failure boundaries.
