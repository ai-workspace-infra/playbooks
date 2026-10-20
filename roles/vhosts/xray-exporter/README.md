# Xray Exporter and Vector snapshots

The normal deployment installs the pinned Exporter and renders its services.
When a host already has a Vector snapshot handoff, a metrics-only invocation
without an explicit credential/handoff is rejected before any deployment change.
This prevents clearing the on-disk credential and silently disabling collection
at the next restart. Metrics-only hosts without an existing handoff remain supported.

`repair_xray_snapshot_credentials.yml` is the bounded repair entrypoint. Supply
the reviewed inventory and exact host limit, `xray_snapshot_repair_hosts`,
`xray_snapshot_repair_authorized=true`, and a fixed GitOps checkout/SHA through
`xray_snapshot_gitops_checkout` / `xray_snapshot_gitops_sha`. It validates the
generation-independent GitOps credential contract and reads only its existing
canonical Vault owner. No token is accepted through an arbitrary extra-var.

The repair changes only `INTERNAL_SERVICE_TOKEN` in the existing root-only
Exporter environment file, retains a protected backup, and restarts only the
two Exporter instances if the value changed. It does not upgrade binaries,
restart Xray/Agent/Vector, alter resource pools, change DNS, enable Billing pull,
or write business data. Authenticated latest-snapshot probes expose only safe
timestamps, counts and aggregate counters. Repeating the same input should
produce no changes or restarts.

An empty sample set is not proof of a broken pipeline or end-to-end success.
Verify real counter increments, Vector delivery, database bucket deltas and
Accounts separately over at least two collection cycles. Do not manufacture
billable traffic to make the result nonzero.

On failed validation, stop and retain the protected backup and evidence; do not
clear counters, delete snapshot stores, refill quota or rewrite financial data.
Rollback uses reviewed prior configuration, never an automatically generated key.
