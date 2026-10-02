# scripts/pipeline

Ansible-phase steps that the `platform-ops-toolkit` workflows run. The toolkit checks
this repository out as `playbooks/` and calls these scripts with `working-directory:
playbooks`, so relative paths such as `../cmdb/inventory.ini` resolve against the
toolkit workspace layout, not against this repository.

| Script | Step |
|--------|------|
| `bootstrap-node.sh` | base bootstrap of a matrix host |
| `deploy-action-runner.sh` | self-hosted runner install |
| `deploy-monitor-agent.sh` | observability agent (retries on SSH rc=4) |
| `verify-xray-billing-chain.sh` | Xray billing ingest verification |
| `switch-cloudflare-dns-records.sh` | Cloudflare DNS cutover via `cloudflare_dns_*` playbook vars |
| `remove-temporary-agent-controller-host.sh` | tear down the temporary controller host |
| `install-email-dns-deps.sh` | collections needed by `configure_email_dns.yml` |
| `lib/require-env.sh` | `require_env` guard, sourced by the scripts above |

`lib/require-env.sh` is a byte-identical copy of the one in `iac_modules` and in
`platform-ops-toolkit/.github/scripts/lib/`; each repo keeps its own so a pinned ref
never depends on another repo's layout.

The inventory fallback `../platform-ops-toolkit/inventory.ini` in the two DNS scripts is a
local-development path (sibling checkouts) and is only used when no CMDB inventory exists.

## Tests

`scripts/pipeline/tests/*_test.sh` run in `.github/workflows/pipeline-scripts.yml`.
