# Resend transactional email DNS

`configure_resend_dns.yml` is the imperative reconciler for the declarative policy at
`gitops/resources/xworktech.com/prod/cloudflare/email-dns.yaml`.

It creates/fetches the `xworktech.com` domain in Resend, reads the current verification
records from Resend, and upserts them in Cloudflare together with DMARC and Google Workspace
MX records. It never deletes unrelated records. The four approved From addresses are
`no-reply@`, `support@`, `billing@`, and `security@` at `xworktech.com`; applications should
set `Reply-To: support@xworktech.com` where replies need to reach Workspace.

## Secrets and execution

Vault KV path (override with `RESEND_VAULT_PATH`):

```text
kv/data/prod/xworktech-email
  resend_api_key
  cloudflare_api_token
```

The Cloudflare token needs Zone:Read and DNS:Edit for `xworktech.com`; the Resend key needs
domain management/read access. For local bootstrap, the same names may be supplied through
`RESEND_API_KEY` and `CLOUDFLARE_DNS_API_TOKEN`.

The supported application path is the GitHub Actions workflow
`.github/workflows/configure-resend-dns.yml`. GitHub Actions exchanges its OIDC JWT for the
`github-actions-gitops-prod` Vault role, injects the two short-lived secret values, and then
executes:

```bash
ansible-playbook -i localhost, -c local playbooks/configure_resend_dns.yml
```

Do not add `VAULT_TOKEN` to GitHub repository secrets. A local run may use the same playbook
with an operator-approved Vault token, but that is not the CI deployment path.

Resend may return an SPF include that differs by account/region. Merge it into the one apex
SPF record alongside `include:_spf.google.com`; never publish two `v=spf1` TXT records.
