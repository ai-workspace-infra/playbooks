# Transactional email DNS

`configure_email_dns.yml` is the imperative reconciler for the declarative policies at
`gitops/resources/<zone>/<env>/cloudflare/email-dns.yaml`. Two zones are declared today:
`xworktech.com` and `svc.plus`.

It reconciles Google Workspace MX, the merged apex SPF, DKIM selectors and DMARC. Where a
policy declares `spec.resend`, it also creates/fetches that domain in Resend and publishes the
verification records Resend issues; a policy without that block skips the Resend API entirely
and needs no Resend key. No policy declares it at present.

`svc.plus` is a Google Workspace **user alias domain** of `xworktech.com`, so a single mailbox
answers at both. A domain alias does not inherit its parent's DKIM key: each zone carries its
own selector, generated in the Admin console. A DKIM entry whose `content` is still empty is a
key nobody has generated yet — the reconciler skips it and says so, rather than publishing a
broken selector.

Applications should set `Reply-To: support@xworktech.com` where replies need to reach a human;
the sending identity itself is a no-reply alias.

## Secrets and execution

Cloudflare credentials come from the entry the serverless orchestrator already reads, rather
than a mail-specific copy — one credential in two paths is two rotation points, and the second
is the one nobody remembers on rotation day:

```text
kv/data/<env>/serverless/cloudflare
  CLOUDFLARE_API_TOKEN        # needs Zone:Read + DNS:Edit on every declared zone
```

A policy that declares `spec.resend` additionally needs a Resend key. None does today, and no
Vault path holds one: `kv/data/<env>/xworktech-email` was referenced by the pipeline this
replaced but never actually existed — which is why that pipeline failed at the Vault step with
`not found` rather than a permissions error. Supply `RESEND_API_KEY` through the environment,
or point `RESEND_VAULT_PATH` at a path you have created.

For local bootstrap, both values may be supplied through `CLOUDFLARE_DNS_API_TOKEN` and
`RESEND_API_KEY` directly.

The supported application path is the workflow **Configure Email DNS** in
`ai-workspace-infra/platform-ops-toolkit` (`.github/workflows/configure-email-dns.yaml`). It
lives in the orchestration repository, not here: this repository holds Ansible content, and
every other pipeline that runs it is driven from there. GitHub Actions exchanges its OIDC JWT
for the `github-actions-platform-ops-toolkit-<env>` Vault role, injects the short-lived token,
and runs one zone at a time:

```bash
ansible-playbook -i localhost, -c local playbooks/configure_email_dns.yml
```

Dispatch is restricted to release tags by that role's `ref` bound claim, so the workflow cannot
be run from `main`. The role's workflow allowlist lives in the toolkit's
`scripts/create_vault_service_repo_roles.sh`.

Do not add `VAULT_TOKEN` to GitHub repository secrets. A local run may use the same playbook
with an operator-approved Vault token, but that is not the CI deployment path.

## Two things this reconciler will not do

**It will not touch the apex TXT name.** That name holds the merged SPF plus whatever
domain-verification tokens the zone carries for Search Console, Workspace or anything else —
`svc.plus` has two. Reconciling it by name would take those out as collateral, so the apex TXT
is excluded from the owned set and a declared record that lands there fails the run.

**It will not publish two `v=spf1` records.** The apex SPF is merged from whichever providers
the policy declares and written as a single record. A zone with two `v=spf1` TXT records fails
SPF outright at the receiver, which is worse than having none.
