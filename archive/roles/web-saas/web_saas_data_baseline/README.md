# Web SaaS UAT data baseline manifest

This role validates and writes a write-once manifest receipt for approval and
provenance metadata only. The receipt status is `manifest_validated`; it does
not connect to PostgreSQL, copy rows or snapshots, initialize a schema, or seed
data. The manifest must identify a UAT source, a distinct target, snapshot,
configuration revision, approved allowlist, sanitization policy, unique
baseline, approval metadata bound to that exact manifest, and nonzero user and
subscription sample counts.
PROD as the source environment is rejected.

The caller supplies `web_saas_data_baseline_manifest`, sets
`web_saas_data_baseline_environment: uat`, and chooses a new
`web_saas_data_baseline_receipt_file` on the controller. Existing receipt paths
are refused so a previously recorded manifest cannot be overwritten. The JSON receipt records
that no data copy, database initialization, or seed was performed. Counts and
approval references are claims from the supplied evidence; this role does not
independently query the database, authenticate the approver, or prove that a
database baseline was frozen. PROD-to-UAT initialization is a separate
explicitly approved workflow and is not implemented here.

The validation contract is in `files/validate_contract.py`; it reads JSON from
stdin and emits only the normalized metadata receipt. Keep credentials and raw
user data out of the manifest.
