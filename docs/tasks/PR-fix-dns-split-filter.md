# PR: fix/dns-split-filter

## Summary
Fix `split` filter crash in `update_site_dns.yml` when handling list-type `service_domains` from the CMDB inventory.

## Context
During the final `Sync Cloudflare DNS` step of the pipeline, the playbook `update_site_dns.yml` attempts to build the list of DNS records to reconcile. It parses `service_domains` for each inventory host:
```yaml
cloudflare_dns_records: >-
  ...
  {%- set service_domains = (host_data.service_domains | default('') | split(',')) ... -%}
```

If `service_domains` is defined as a list/sequence in the CMDB inventory (rather than a comma-separated string), calling the `split` filter on it fails:
```
The filter plugin 'ansible.builtin.split' failed: descriptor 'split' for 'str' objects doesn't apply to a '_AnsibleLazyTemplateList' object
```

The fix adds defensive checks to identify the variable type:
- If `service_domains` is a string, it applies the `split(',')` filter.
- Otherwise, it treats it as a list directly.

## Changes
| File | Change |
|------|--------|
| `update_site_dns.yml` | Update `service_domains` parsing logic with type checks. |

## Related
- GHA Run: 29151750477
- Playbooks PR: #124
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- CI/CD workflow `deploy-ai-workspace-iac.yaml` should deploy and reconcile DNS successfully.
