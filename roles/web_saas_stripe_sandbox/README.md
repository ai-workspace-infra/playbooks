# Recovered V3 → Stripe Sandbox host preparation

`web-saas-stripe-sandbox.yml` uses the IaC `inventory/terraform_cmdb.py`
inventory. It accepts only one `console-selfhost-prod-v3.<domain>` record,
`cmdb_name=web-saas-prod-v3`, group
`web_saas_recovery`, and exactly four V3 `service_domains`: console, accounts,
billing, postgresql. Actual hostname must be `web-saas-prod-v3`, with `/data`
mounted and PostgreSQL using `/data/postgresql`. Source V2 is never a play
target or a database/network endpoint. No cloud/DNS/firewall operation exists.

`web_saas_stripe_manifest_file` is required: an absolute controller path to
the reviewed GitOps `compose/web-saas/recovery/prod-v3-stripe-sandbox.yaml`
(`kind: StripeSandboxQualification`). Domains, Webhook path/registry selection,
Sandbox mode, API version, Vault path/field names and Doco target come from
this declaration. Both Ansible and the host executor validate a strict V3 / test
KV/key allowlist and require the resolved runtime value
`stripe.webhook_url == 'https://' + target.accounts_domain + stripe.webhook_path`.
`web_saas_stripe_webhook_registry_file` is required: the absolute controller
path to GitOps `compose/web-saas/billing/stripe-webhooks.yaml` with
`kind: StripeWebhookRegistry`. The registry domain_base/path must match the V3
manifest's domains/path. Only `stripe.webhook_environment=prod-v3`, equal to
target.generation, is selected. The manifest declares `webhook_registry_ref`
and must not contain a second `webhook_url`. The role merges the selected URL
into an in-memory runtime declaration; it never edits either input file.
The retained V2 registry entry is data only and never becomes an execution target.
No independent generated Webhook URL overrides the registry. The resolved digest
and nonsecret Webhook URL are saved in checkpoint and prepare receipts;
prepare/verify output returns the declared `webhook_url` without key values.

`web_saas_stripe_iac_receipt_file` is also required. The role reads the absolute
inventory path from controller environment `AI_WORKSPACE_CMDB_JSON`, hashes
that original file, and requires equality to `receipt.inventory_sha256`.
The paired receipt must say environment `prod`, result `applied_verified`,
project `svc.plus`, provider `gcp-cloud`. Older flat CMDB artifacts without
top-level environment are accepted; a populated CMDB environment must agree
with the receipt. No inventory fields or original artifact are rewritten.

The role does not select a Doco-CD release, restart/reload a service, or deploy
an application. It supplements `web_saas_generation_recovery`; do not rerun
that role's read-only verification after Sandbox writes have begun.

## Phases and gates

* `preflight` (default): inventory and actual host/mount identity only.
* `prepare`: requires the Toolkit caller to supply only the required fields
  from Vault path `kv/CICD/domains/svc.plus` through a protected runtime vars
  file, then restores them through the existing `caddy_certificate_restore`
  Role. The Role never reads Vault; certificate values are never stored in
  GitOps, argv, artifacts, or logs. `prepare.py` validates the candidate Caddyfile in a temporary
  network-isolated container using the live immutable Caddy image and the
  restored certificate mounted read-only. It does not reload Caddy or contact
  ACME.
* `prepare` also reads only `SANDBOX_STRIPE_SECRET_KEY` and
  `SANDBOX_STRIPE_WEBHOOK_SECRET` with `vault kv get -field=...` from
  `kv/prod/billing-service` on the authenticated controller. There is no full
  KV JSON read or live-key field access. Vault's KV API itself has document-level
  access; enforce the appropriate runtime Vault policy on the caller.
  Secret tasks use `no_log`; values travel via stdin, never process arguments.
  Then run the locked root executor on V3:
  1. Verify the previous recovery DB identity and ordinary `recovery_app`
     role's existing SELECT/INSERT grants. No password or grant is changed.
  2. Export a repeatable-read snapshot, record exact clean schema, users and
     all public/finance table counts, and produce `pg_dump --format=custom`
     using that same snapshot. Schema is taken from the declaration (currently
     `2026101703`); users are measured by default. Optional
     `web_saas_stripe_users` asserts an exact positive count only when supplied.
     All finance and historical public billing tables are counted and preserved,
     including nonempty invoice/payment/refund tables; no empty-finance gate exists.
     Subscription counts are measured, then compared exactly after restore.
  3. Create an internal Docker network and temporary PostgreSQL container
     using the live container's exact local image ID. It has an independent
     anonymous data volume, no bind mounts, no ports, no production network
     or credentials. Restore into a new `account` DB and compare PostgreSQL
     engine version, schema version/dirty and every recorded count. Remove
     only these randomly named disposable resources; retain the checkpoint.
  4. Validate candidate Caddy config with the exact running Caddy image, without
     reload. Save private copies of existing host config. Write only the new
     `stripe-sandbox.env`, updated `account.yaml`, and `Caddyfile`. Set
     `recovery_app`'s global and account-specific
     `default_transaction_read_only=off`; existing sessions take this setting
     only on reconnect. Background writers remain false.
  5. Verify original `secrets.env` checksum is unchanged and write the receipt.
* `verify`: check the receipt, dump checksum, current schema, role setting and
  prepared config checksums; probe Accounts/Billing `/readyz` and Console over
  trusted public HTTPS after the separate GitOps deployment. Counts may change
  through Sandbox tests, so verify does not compare current counts to backup.

Directories are root-owned 0700, files 0600, under
`/data/backups/web-saas/prod-v3/<release-tag>/<run-id>/`. Files include
`account.dump`, `checkpoint.json` (restore evidence), `*.before` (private
configuration backups), and `prepared.json` (host readiness receipt).
Run IDs are single-use, including failed runs. Failure preserves artifacts
and does not automatically restore/overwrite a live database or roll back
partial host changes. Inspect private evidence and use a fresh run ID to retry.
Each host file is replaced atomically; the group of host/role changes is not
one atomic transaction. A failed gate never issues a successful prepare receipt.

Original `secrets.env` is never rewritten, chmodded or replaced. The
Accounts-only `stripe-sandbox.env` copies its existing values and overrides
only Stripe keys plus `STRIPE_FRONTEND_URL` / `STRIPE_FRONTEND_BASE_URL` with
the declared V3 Console HTTPS origin, and declared `STRIPE_API_VERSION`,
`STRIPE_MODE`, `STRIPE_WEBHOOK_URL`. Both `sk_test_` and `whsec_` prefixes are required;
live keys, whitespace and newline injection fail. Auth, DB and other values
are preserved. This Accounts env file contains credentials: never print it or
share it with any other container. No Sandbox key is passed to Billing,
PostgreSQL, Console or Caddy by this role.

Caddy serves the shared public SNI names `console.svc.plus`,
`accounts.svc.plus`, and `billing.svc.plus` from the restored wildcard
certificate, alongside the version-specific V3 aliases. These public names are
shared across PROD generations; only the IaC DNS cutover chooses the active
generation. The role does not modify DNS. The fourth PostgreSQL V3 alias is
reserved in a comment with no HTTP/TCP listener. All Console methods and paths go to `console:3000`,
including POST `/api/auth/login`, `/api/auth/stripe/checkout` and
`/api/auth/session`, preserving Next.js cookies and proxy Route handlers.
The Accounts host accepts application routes, including
`/api/billing/stripe/webhook`; signature enforcement belongs to Accounts.
Billing exposes only readiness/health/ping. `account.yaml` keeps existing
credentials/placeholders and removes `:8443` from its V3 URLs.

## Caller variables and invocation

Use an already authenticated IaC CMDB artifact and controller Vault session.
Prepare does not inspect a GitOps checkout: the caller must verify that the
release tag is immutable and matches the reviewed GitOps commit. Example
nonsecret runtime vars (run ID must be new for prepare):

```yaml
web_saas_stripe_target: console-selfhost-prod-v3.svc.plus
web_saas_stripe_generation: prod-v3
web_saas_stripe_domain_base: svc.plus
web_saas_stripe_manifest_file: /tmp/gitops-p0f-v3-stripe-20261018/compose/web-saas/recovery/prod-v3-stripe-sandbox.yaml
web_saas_stripe_webhook_registry_file: /tmp/gitops-p0f-v3-stripe-20261018/compose/web-saas/billing/stripe-webhooks.yaml
web_saas_stripe_iac_receipt_file: /tmp/iac-prod-v3-apply.3NGnEi6R/receipt.json
web_saas_stripe_phase: prepare
web_saas_stripe_release_tag: prod-v3-stripe-sandbox-20261018-r1
web_saas_stripe_run_id: sandbox-20261018-01
web_saas_stripe_users: null  # Default: measure; do not freeze an earlier count.
# Optional: web_saas_stripe_vault_addr: https://vault.svc.plus
```

```sh
AI_WORKSPACE_CMDB_JSON=/tmp/iac-prod-v3-apply.3NGnEi6R/inventory.json \
  ansible-playbook -i inventory/terraform_cmdb.py \
  web-saas-stripe-sandbox.yml -e @/protected/runtime/stripe-v3.yml
```

Reuse the same release tag/run ID with `web_saas_stripe_phase=verify` only
after the separate immutable release selection/reconciliation. Verification
forces each public hostname to the V3 IaC IP using `curl --resolve`; standard
certificate verification stays enabled and public DNS is unchanged. Readiness
does not claim authenticated login, signed webhook delivery or successful
checkout; use a dedicated Sandbox test user for those acceptance steps.

## GitOps / Doco-CD interface (caller-owned)

The separate GitOps change must provide `docker-compose.stripe-sandbox.yml`
and Doco target `prod-v3-stripe-sandbox` with these properties:

* Accounts `env_file: !override` specifies only
  `/etc/xcontrol/web-saas-recovery/stripe-sandbox.env`. Remove/override inherited
  inline empty Stripe environment values, which otherwise win over env_file.
* Only Accounts receives this file and joins `sandbox_egress`; preserve
  background writers false. Allow its Sandbox Stripe HTTPS requests.
* Caddy publishes 80/443 and mounts the prepared Caddyfile plus
  `/etc/xcontrol/tls/svc.plus:/etc/caddy/tls/svc.plus:ro`; preserve persistent
  Caddy data/config volumes. PostgreSQL publishes no 5432 port. Preserve DB/image/volume and
  other services' original env_file, so their config does not change.
* Console browser/server Accounts origins, billing origin and V3 frontend URLs
  omit `:8443`. Preserve existing auth values, volumes and other recovery limits.
* Select the reviewed immutable tag only after a successful `prepared.json`:
  call the existing `select-doco-cd-release.yml` with
  `doco_release_host=console-selfhost-prod-v3.svc.plus`,
  `doco_release_repository=https://github.com/ai-workspace-infra/gitops.git`,
  `doco_release_reference=refs/tags/<release-tag>`, and
`doco_release_target=prod-v3-stripe-sandbox`. Selection itself deploys;
  this role deliberately does not invoke it.

## Local verification

```sh
python3 -m unittest discover -s tests/data_operations -p test_stripe_sandbox.py -v
STRIPE_TEST_DOCKER_IMAGE=postgres:17 python3 -m unittest discover -s tests/data_operations -p test_stripe_sandbox.py -v
ansible-playbook -i localhost, web-saas-stripe-sandbox.yml --syntax-check
```

Tests exercise generated YAML/env/Caddy routes, checksum/permission retention,
actual Ansible inventory rejection before remote tasks, exception redaction,
and two private native PostgreSQL clusters for real dump/restore (including
concurrent writes with exported snapshot). Set `STRIPE_TEST_PG_BINDIR` to a
directory containing the full PostgreSQL server/client tools. Native PG tests
skip if only libpq clients are available. `STRIPE_TEST_DOCKER_IMAGE=postgres:17`
enables a real Docker snapshot/checkpoint/isolated restore test with independent
containers, volumes and internal networks. Public TLS and authenticated flows
still require the target runtime and are not implied by local tests.

Verified locally on 2026-10-18: 27 tests passed with real Docker `postgres:17`
and two native PostgreSQL 17.6 clusters enabled. The additional read-only
controller acceptance test passed against the supplied original IaC inventory,
paired receipt, Sandbox manifest and Webhook registry. No remote host task or
release selection was run by these tests. Disposable DB/network/volume fixtures
and local source-build artifacts were removed after verification.

Receipt count interface (`checkpoint.json`, `prepared.json`, and sanitized
prepare/verify stdout): `counts` is a flat object with fully qualified table
names and integer values, e.g. `{"public.users":35,"public.subscription_records":2,
"public.billing_ledger":1,"finance.invoices":2,"finance.payments":1,
"finance.refunds":1,"public.schema_migrations":1}`. `finance_counts` is its
finance-schema / public-finance-prefix subset. `expected_users` is the measured
`counts["public.users"]` from the exact dump snapshot; E2E callers should read
that integer instead of assuming 32. Nonempty finance amounts/rows are also
checked for preservation by a real Docker 17 invoice/payment/refund test.
