# Web SaaS database preflight

Read-only guard for the existing `account` database. It requires externally
verified source and frozen-baseline identifiers, a reference to an authorized
nonempty subscription sample, and one exact clean migration version. It reports
only database component evidence; it does not verify login, permissions,
entitlements, or G1/G2/G3 acceptance.

Inputs are `web_saas_data_preflight_database` (fixed to `account`),
`web_saas_data_preflight_postgres_container` (fixed to
`web-saas-postgresql`), `web_saas_data_preflight_expected_version`,
`web_saas_data_preflight_source_database_id`,
`web_saas_data_preflight_baseline_id`, and
`web_saas_data_preflight_authorized_subscription_sample_id`. Missing or unsafe
provenance, sample reference, migration state, or nonempty row counts fails
closed. The sample reference is provenance only; this role does not inspect
business semantics or infer approval from a count.

The caller selects the exact host using its authenticated CMDB inventory.
There are no host-side changes to roll back. Example syntax check:

```bash
ansible-playbook --syntax-check -i 'web-saas-uat,' web-saas-release-upgrade.yml
```
