# cloudflare_svc_plus_dns

Specialized wrapper role for the `svc.plus` DNS set.

## What it does

- Loads the managed record manifest from `playbooks/vars/cloudflare_svc_plus_dns.yml`
- Delegates the actual create/update/delete reconciliation to the shared `cloudflare_dns` role

## Entry point

Use `update_cloudflare_svc_plus_dns.yml` to apply this role.
