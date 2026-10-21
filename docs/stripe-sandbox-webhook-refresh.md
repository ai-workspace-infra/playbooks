# Canonical sandbox webhook refresh

Owner: `web_saas_stripe_sandbox` configuration Role. Caller: `configure-stripe-sandbox-webhook.yml`. GitOps owns the canonical URL, event list and Vault field references; this Role executes the configuration change for one explicitly selected existing host.

Use a cloud-control-plane-verified one-use inventory and a clean GitOps checkout pinned to its exact commit. Required variables are `web_saas_stripe_host`, `web_saas_stripe_gitops_checkout`, `web_saas_stripe_gitops_sha` and `web_saas_stripe_runtime_env`. The runtime file must be an existing root-owned 0600 credential file from the reviewed allowlist.

Run syntax check, then `--check` before apply. Read-only identity, Vault and Stripe checks still execute in check mode; file and provider changes do not. The Role verifies a test API key against the running Accounts container, stages only the webhook secret with a private backup, and updates only the existing canonical test endpoint's event subscriptions. It does not create endpoints, change live credentials, restart services, migrate a database or reconcile payment records.

After staging, select the reviewed immutable application release through the existing Doco-CD release-selection Playbook. Verify runtime key mode and secret equality without exposing values. Resend only the affected test payment events using Stripe's official delivery mechanism, then confirm the local subscription, allowance, node access and completed purchase reservation. A browser success return alone is insufficient acceptance.

Credentials retain test/live names under the existing Vault owner. Deployment generation numbers do not appear in secret field names. Rollback restores the private runtime-file backup and the previous immutable release; do not print the backup contents.
