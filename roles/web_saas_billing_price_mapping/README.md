# Provider price references for restored generations

`web-saas-billing-price-mapping.yml` applies a reviewed Test-mode price map from
Accounts' `stripe-sync-catalog.sh --expected-account ... --price-map-output ...`.
The caller supplies the selected `prod-vN` host, immutable release, exact clean
migration version and the parsed map. Run only after the encrypted checkpoint
and isolated restore gate and after verifying the map against Stripe Test API.

The executor locks the catalog, checks every active plan's amount/currency/unit,
rejects conflicting existing provider references, then updates only missing
`stripe_price_id` values and records one `audit_logs` entry in the same transaction.
It never changes users, subscriptions, financial records, wallets or quotas.
Repeated identical input changes zero rows and creates no duplicate audit entry.

Generation backup keys are stored by `web_saas_backup_key` at
`kv/<prod-vN>/backups`, key `web_saas_release_passphrase`. Missing paths are
initialized with a cryptographically random key using Vault CAS=0, then read back
before backup. Existing or unreadable paths are never overwritten. Values remain
in memory and no-log tasks; only the KV reference belongs in release evidence.
