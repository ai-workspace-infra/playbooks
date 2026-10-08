# PROD Accounts SMTP recovery

The `prod-services` Doco-CD target reads `/etc/xcontrol/web-saas/services.env`,
not the generic stack's `secrets.env`. Both the full application convergence
and `repair-web-saas-prod-smtp.yml` read the SMTP references from the reviewed
GitOps runtime selection and fetch `username` and `password` from
`kv/prod/platform/smtp/google` at execution time.

For SMTP-only recovery, use an immutable Playbooks `v*` checkout and set
`GITOPS_CHECKOUT`, `GITOPS_REF=refs/tags/v...`, and a runtime-only `VAULT_TOKEN`.
Use the reviewed inventory and strict SSH host-key checking, then run:

```bash
ansible-playbook -i /path/to/reviewed-inventory.ini repair-web-saas-prod-smtp.yml
```

This entrypoint updates only SMTP keys in the existing `services.env`, renders
Accounts' configuration template, and repoints Doco-CD to the immutable
application-only GitOps release. Compose must recreate Accounts to load its
updated environment; `docker restart` retains the old environment.

The recovery requires a missing-code registration request to return
`400 verification_required`, and verifies that PostgreSQL's container ID,
start time and mounts remain unchanged. Afterward, send one verification code
to the requested recipient through the registration endpoint and inspect
Accounts' delivery result. SMTP acceptance does not by itself prove inbox
delivery. The screenshot's recipient `156405189@qq.com` receives mail in QQ;
the Gmail sender account's inbox is not the recipient inbox.
