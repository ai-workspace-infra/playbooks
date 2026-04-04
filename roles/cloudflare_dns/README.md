# cloudflare_dns

Reusable Ansible role for creating and updating Cloudflare DNS records in the `svc.plus` zone.

## What it manages

- Zone lookup by name, or direct `cloudflare_dns_zone_id`
- Create/update/delete of managed DNS records
- Token resolution from Ansible extra vars:
  - `-e CLOUDFLARE_DNS_API_TOKEN=...`
  - `-e CLOUDFLARE_API_TOKEN=...`
- Environment-backed token resolution as fallback:
  - `CLOUDFLARE_DNS_API_TOKEN`
  - `CLOUDFLARE_API_TOKEN`

## Important variables

- `cloudflare_dns_records`
  - List of records to manage.
- `cloudflare_dns_zone_name`
  - Cloudflare zone name. Default: `svc.plus`
- `cloudflare_dns_zone_id`
  - Optional direct zone id to skip lookup.
- `cloudflare_dns_api_token`
  - Optional explicit token. If omitted, the role first checks Ansible extra vars, then falls back to environment variables.

## Example

```yaml
---
- name: Update DNS
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    cloudflare_dns_records:
      - type: A
        name: jp-xhttp-contabo.svc.plus
        content: 46.250.251.132
        ttl: 1
        proxied: false
  roles:
    - role: cloudflare_dns
```

## Recommended usage in this repo

Run the repo-level playbook and pass the token via `-e`:

```bash
ansible-playbook -i inventory.ini update_cloudflare_dns.yml \
  -e CLOUDFLARE_DNS_API_TOKEN=your_token
```

You can also do a dry run:

```bash
ansible-playbook -i inventory.ini update_cloudflare_dns.yml \
  --check \
  -e CLOUDFLARE_DNS_API_TOKEN=your_token
```
