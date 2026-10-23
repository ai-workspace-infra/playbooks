# `xray-edge`

Adds one standalone HTTPS site to an already-running Caddy instance and streams only the configured XHTTP path to one configured upstream. Caddy terminates public TLS on both legs; the upstream uses HTTP/2 over TLS with an explicit SNI and Host value. The role does not install Caddy or Xray, change the main Caddyfile, manage DNS/firewall/cloud resources, provision Exit users, or configure the JP node.

The role currently supports **diagnostic mode only**. It fails closed if a caller requests `xray_edge_mode: pro`: the production entitlement and admission service is not implemented. Hiding a URI from non-PRO users is not authorization.

## Inputs

| Variable | Required/default | Meaning |
| --- | --- | --- |
| `xray_edge_hosts` | Required by caller | Explicit inventory host or group; `all` and `*` are rejected. The selected host/group must exist in the inventory used for this run. |
| `xray_edge_domain` | Required | Public Edge hostname whose TLS certificate Caddy will obtain/manage. |
| `xray_edge_upstream_address` | Required | Upstream host used in the TLS connection. |
| `xray_edge_upstream_host` | Upstream address | HTTP Host header sent upstream. |
| `xray_edge_upstream_sni` | Upstream Host | TLS server name verified upstream. |
| `xray_edge_upstream_port` | `443` | Upstream HTTPS port. |
| `xray_edge_path` | `/split` | Only this path and descendants are forwarded; all other paths return 404. |
| `xray_edge_mode` | `diagnostic` | `pro` is rejected until server-side entitlement enforcement exists. |
| `xray_edge_caddyfile_path` | `/etc/caddy/Caddyfile` | Existing main configuration, read-only. |
| `xray_edge_caddy_conf_dir` | `/etc/caddy/conf.d` | Existing imported fragment directory. |
| `xray_edge_site_filename` | `xray-edge.caddy` | The single fragment owned by this role. |

The main Caddyfile must import `<conf_dir>/*.caddy`. The role validates the proposed fragment together with the other current fragments, excluding only its own existing fragment. It then installs the candidate with Ansible's atomic file replacement, validates the live main configuration, and reloads Caddy only when the site changed. A validation or reload failure restores the role-owned backup (or removes a newly created fragment) and reloads the prior configuration.

When adopting this role for an already-running Edge, set `xray_edge_site_filename` to the exact existing Edge fragment filename. The role replaces only that file; leaving the generic default while an older same-domain fragment remains may create a duplicate site definition and will be rejected by combined validation.

## Run

Pass the target explicitly with `-e xray_edge_hosts=...`. The value can be a CMDB group/hostname or a host/group from another inventory. Dynamic CMDB inventory is supported, but not mandatory; use the inventory that actually contains the selected target. Ansible inventory selection is independent of this variable; pass `-i` when the target is not in the repository-configured default inventory.

```bash
ansible-playbook -i inventory/terraform_cmdb.py deploy_xray_edge.yml \
  -e 'xray_edge_hosts=cmdb-edge-host-or-group' \
  -e 'xray_edge_domain=edge.example.net' \
  -e 'xray_edge_upstream_address=exit.example.net' \
  -e 'xray_edge_upstream_host=exit.example.net' \
  -e 'xray_edge_upstream_sni=exit.example.net' \
  -e 'xray_edge_site_filename=edge-existing-site.caddy'
```

For a static inventory, select the matching host/group instead, for example `-i inventory.ini -e 'xray_edge_hosts=jp_xhttp_contabo_host'`. For a one-off host that is not already in a configured inventory, use a single-host inline inventory such as `-i '203.0.113.10,' -e 'xray_edge_hosts=203.0.113.10'`; verify the host identity and SSH settings before applying.

Run `--syntax-check` before applying. Tags `configure` and `validate` are both included on the candidate-render/combined-validation path so a normal tagged configuration cannot bypass pre-install validation. `--check` performs preflight only because the combined Caddy check requires a temporary remote candidate.

## Rollback

Restore the previous version of only `{{ xray_edge_caddy_conf_dir }}/{{ xray_edge_site_filename }}` from the backup recorded by Ansible, validate the unchanged main Caddyfile, then run `caddy reload --config /etc/caddy/Caddyfile`. If the site did not exist before deployment, remove only this role-owned fragment. Existing Caddy sites and the upstream Exit are outside the rollback scope.

The role does not change the client profile. Client routing back to the existing Exit is a separate user-controlled action.
