# Agent Proxy Role

This role owns XConnect Edge Agent and Xray host installation, runtime
configuration, service lifecycle, standalone Xray mode, binary-only upgrades,
and the installer network baseline. The entry playbook selects the operation;
this role contains its host changes.

## Operations

| `agent_proxy_operation` | Behavior |
| --- | --- |
| `deploy` | Reconcile Caddy, Xray, Agent configuration, Accounts registration, and Xray services. |
| `standalone` | Install Caddy and Xray without an Agent or Accounts registration; preserve a generated UUID and print XHTTP/TCP links. |
| `upgrade` | Replace the staged Agent binary and install the pinned Caddy/Xray releases when safe; preserve Agent/Xray/Caddy configuration and restart changed services. A Caddy build with Layer 4 is preserved. |
| `print-arch` | Report the target architecture and matching runtime asset without changing the host. |

## Inputs

- `agent_proxy_operation`: one of the four operations above; defaults to `deploy`.
- `agent_proxy_binary_src`: optional pre-staged Agent binary. When unset, the local installer resolves the GitHub release and verifies its asset digest inside this role.
- `agent_proxy_primary_domains`: exactly one public node domain for managed/standalone setup.
- `agent_proxy_open_stunnel_5443`: optionally adds `5443/tcp` to an already active UFW firewall.
- `agent_allowed_emails`: normalized list for the managed local-installer path; an empty list remains empty.
- `vault_agent_tls_enabled`, `vault_agent_tls_secret_path`, `VAULT_ADDR`, and `VAULT_TOKEN`: optional Vault TLS delivery inputs. Secrets remain in Ansible memory or the protected Vault Agent token file.
- `xray_version` and `xray_release_sha256`: checksum-pinned Xray release inputs from role defaults.

## Preconditions and evidence

The caller supplies a local inventory for a single-host setup or a CMDB-backed
inventory for managed delivery. `deploy` requires the Accounts URL and a valid
runtime service token. `standalone` requires a node domain and either a usable
Vault TLS certificate path or outbound ACME access. `upgrade` requires an
existing Agent service. `print-arch` requires no
credentials and makes no host changes.

The role validates Xray configuration before starting Xray, registers managed
Agent status with Accounts, and relies on Ansible task results plus systemd
state for service evidence. Re-running an operation converges the same
configuration; `upgrade` does not render or overwrite configuration files.

## Example

```bash
AGENT_PROXY_OPERATION=deploy \
AGENT_ID=node.example.test \
AGENT_PROXY_DOMAIN=node.example.test \
AGENT_CONTROLLER_URL=https://accounts.example.test \
ansible-playbook deploy_xray_proxy_server.yml \
  -i localhost, --connection local \
  -e 'agent_service_hosts=localhost agent_proxy_local_installer=true agent_proxy_operation=deploy'
```

## Rollback

Re-run `deploy` with the previous verified Agent artifact and the previous
Playbooks commit. The operation does not delete Xray configuration or
credentials. For `standalone`, switch to a reviewed managed deployment before
enabling Accounts registration; DNS changes remain owned by IaC and are not
performed by this role.
