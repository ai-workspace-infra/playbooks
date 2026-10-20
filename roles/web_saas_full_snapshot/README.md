# Generation full logical snapshot

`web-saas-full-snapshot.yml` runs this role locally on the explicitly selected
generation over verified SSH. It checks the actual hostname and independent
`/data` mount before taking a checkpoint. The source remains online.

The `capture` phase uses one exported read-only MVCC snapshot for the custom
database dump and all table-row fingerprints. Archives, PostgreSQL globals and
configuration are encrypted with a runtime-only backup passphrase. An isolated
container using the exact current PostgreSQL image restores the archive with
networking disabled, no host ports and no production mounts. Counts and full
row fingerprints must match. Existing business images, start times and restart
counts must remain unchanged.

The `finalize` phase receives the separately encrypted Vault snapshot, inspect
result and path/key-name metadata through protected input, adds encrypted
container metadata, verifies the unchanged business processes, and writes and
checks SHA256SUMS. Missing optional KV metadata is marked unavailable. A Vault
inspect/round trip is not an independent Vault restore/unseal drill.

Inputs: `snapshot_generation`, `snapshot_expected_schema`, unique numeric
`snapshot_run_id`, `snapshot_passphrase`; `snapshot_phase` defaults to capture.
Finalize also requires `snapshot_finalize_payload`. Pass secrets via a private
0600 temporary variables file, never argv, logs or committed configuration.
Receipts are root-only under `/data/backups/web-saas/<generation>/receipts/`.

Use `scripts/data_operations/capture_generation_vault_checkpoint.py` to prepare
the separate encrypted Vault evidence, and
`scripts/data_operations/finalize_generation_checkpoint.py` to invoke the
staged Playbook over SSH. Both remain operator entrypoints; they do not deploy
an application, modify DNS, stop the source or restore shared Vault.

Snapshot directories are unique; the database capture uses a nonblocking host
lock. Finalization refuses overwriting completed artifacts. Failed verification
retains the run-owned restore container and checkpoint for investigation.
This logical backup excludes cloud disk images, image layers and inactive
databases. Sequence values and Vault/runtime captures are not a global atomic
database boundary. Review roles, ACLs and sequence behavior separately before
production cutover.
