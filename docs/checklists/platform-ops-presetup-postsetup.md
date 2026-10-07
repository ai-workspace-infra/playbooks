# Platform Operations `presetup` / `postsetup` checklist

This checklist is the Playbooks owner contract for release operations. The
Toolkit may dispatch a pinned workflow and collect a sanitized receipt; it does
not execute or accept any of the checks below.

Each checklist item belongs to the role that owns the side effect. A role may
fail the workflow when its own evidence is missing. The checklist must never
pause `web-saas-caddy`; Caddy remains in the service graph throughout data
migration and verification.

## `presetup`

| Boundary | Role category | Required evidence before deployment or migration |
| --- | --- | --- |
| IaC resources | `cloud_vm_inventory_emit`, `cloud_vm_request_validate`, `cloud_cli_prereqs` | Target resource IDs, region, network, attached storage, and immutable CMDB digest are present. IaC remains the provider mutation owner. |
| Host runtime | `vhosts/web_saas_host_config`, `vhosts/caddy`, `vhosts/accounts_service`, `vhosts/billing-service`, `vhosts/apisix_service` | Target host is reachable; required containers/services and listeners are present; Caddy is running and excluded from writer-pause operations. |
| Database | `web_saas_data_preflight`, `web_saas_data_baseline`, `web_saas_data_backup`, `web_saas_prod_storage`, `vhosts/postgresql_service` | Read-only connectivity succeeds; baseline and backup receipts are bound to the target; migration scope excludes `web-saas-caddy`; no destructive reset is permitted. |
| API | `vhosts/accounts_service`, `vhosts/billing-service`, `vhosts/apisix_service` | Accounts and Billing health endpoints, configured upstreams, and service credentials are ready for the selected release tag. |
| DNS / edge | `cloudflare_dns`, `vhosts/alicloud_dns_record`, `vhosts/alicloud_dns_sync` | Current records, intended CNAME/upstream targets, TTL, and rollback values are recorded. DNS change remains explicit and manual or explicitly requested by Toolkit input. |
| Business workflow | `web_saas_release_upgrade`, `web_saas_managed_runtime_qualification` | Release tag, owner workflow ref, writer-pause scope, and operator test plan are recorded. Product login, subscription, quota, billing, and single-writer checks remain operator-owned. |

## `postsetup`

| Boundary | Role category | Required evidence after deployment or migration |
| --- | --- | --- |
| IaC resources | `cloud_vm_inventory_emit`, resource-specific provider workflow | The target resource and CMDB digest still match the requested release; no untracked resource mutation occurred. |
| Host runtime | `vhosts/web_saas_post_deploy_readiness`, `vhosts/agent_proxy_post_dns_readiness`, `vhosts/caddy` | Target containers are healthy, listeners are reachable, Caddy is still running, and TLS handshake/certificate/HTTPS response pass against the target. |
| Database | `web_saas_data_migration`, `web_saas_data_restore_verify`, `web_saas_full_business_transfer`, `web_saas_native_*` | Only the reviewed operation ran; receipt is complete; read-only comparison proves count, email, password hash, and Proxy UUID equality; writer pause is released only by the owner workflow. |
| API | `vhosts/accounts_service`, `vhosts/billing-service`, `vhosts/apisix_service` | Accounts and Billing health APIs respond through the intended target/upstream path. |
| DNS / edge | `cloudflare_dns`, `vhosts/alicloud_dns_sync`, `vhosts/web_saas_post_deploy_readiness` | Public edge records and API CNAMEs match the requested switch, and the prior values can be restored. Toolkit does not infer a DNS switch from deployment success. |
| Business workflow | `web_saas_managed_runtime_qualification`, service-specific role checks | Operator records login, subscription, quota, billing, and single-writer results. These are independent of the automated infrastructure/data receipt. |

The Playbooks workflow that owns a checklist must publish the role name, target,
release tag, correlation ID, and evidence paths in its receipt. A dispatch or
queued child run without that receipt is not acceptance.
