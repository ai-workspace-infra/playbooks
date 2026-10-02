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

## Repository boundary and change order

This directory owns Ansible-phase behavior only. Terraform and provision-phase behavior
stays in
[`iac_modules/scripts/pipeline/`](https://github.com/ai-workspace-infra/iac_modules/tree/main/scripts/pipeline),
orchestration and GitOps readers stay in
[`platform-ops-toolkit/.github/scripts/`](https://github.com/ai-workspace-infra/platform-ops-toolkit/tree/main/.github/scripts),
and [`gitops`](https://github.com/ai-workspace-infra/gitops) contains YAML/Markdown
desired-state data only.

For a cross-repository change, merge the IaC and playbooks additions first, then update
the dependent toolkit call sites. Record those dependency PRs and the merge order in the
toolkit PR. New scripts use short-hyphen names, have a test under `scripts/pipeline/tests/`,
and are `100755` when called directly. Do not add one-line wrappers or compatibility
shims.

`lib/require-env.sh` is intentionally byte-identical to the copies in iac_modules and
toolkit, but remains local to this checkout. Branch and release rules are maintained in
[`skills/release-branch-policy/SKILL.md`](../../skills/release-branch-policy/SKILL.md).
