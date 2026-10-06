# Billing additive native schema before business copy

This owner adds only the fixed Billing `cloud_vendor_costs` migration after
Accounts native initialization. Toolkit must first verify the accepted resource,
standby and native-init receipts and this run's independent production approval.
IaC owns opening and revoking same-run access; this owner never changes cloud,
DNS, or source data.

The Billing checkout must be clean and match the reviewed full commit. Its
manifest and exact SQL bytes must match the caller contract. The prebuilt Accounts
image is digest-bound and its compiled native manifest is qualified without
network or production volumes. Server ENTRYPOINT is overridden.

Preview makes no schema changes. Apply requires an independent data gate and
uses only `migratectl migrate` with one mounted read-only reviewed migration,
exact expected/target versions and SHA-256, lock/statement timeouts, and a private
temporary target DSN. It accepts only the exact 52 native Accounts tables at clean
2026100601, or the already-applied 53-table state at clean 2026100701, with zero
business rows. All application/CD writers must remain stopped. No historical
migration replay, business seed, destructive down, reset, source copy or cutover
occurs. Owned execution containers and private temporary files are removed on
failure. Qualification tests are not production acceptance.
