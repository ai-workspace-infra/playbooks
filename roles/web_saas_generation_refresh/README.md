# Authorized recovery-generation refresh

Owner: Playbooks. Caller: `web-saas-generation-refresh.yml`, using IaC-generated
inventory and an explicit V3-only contract. No cloud resource, source-host, DNS
or GitHub Actions deployment operation exists in this role.

This supplements (does not weaken) the first-restore role. Current phases:

- `snapshot`: a PostgreSQL17 client on the V3 host reads V2 through an existing
  authenticated loopback tunnel. An exported REPEATABLE READ READ ONLY snapshot
  binds every public/finance table fingerprint and pg_dump. Encryption streams
  directly to a private V3 `/data/backups/web-saas/prod-v3/<tag>/<run>/` archive.
- `restore_candidate`: restores that archive transactionally into a new,
  run-specific `account_candidate_<run>` database, refuses an existing name,
  and verifies the exact source version, counts and full business-row hashes.

Both require the current run's V3 encrypted backup and actual restore proof,
the exact PostgreSQL system identifier and stopped target writers/Doco-CD.
Secrets enter through stdin; transient source clients inherit credentials via
environment names only. No plaintext archive is created. Failure retains the
old database, candidate and all checkpoint files for inspection.

The generic Docker PostgreSQL client/snapshot implementation is reused from
the existing `web_saas_stripe_sandbox` owner file, staged as a Python library;
its Sandbox preparation/deployment entry point is never invoked.

Sequence counters are not MVCC data. pg_dump preserves its observed sequence
values; the source is never advanced with nextval. Row fingerprints are exact
at the exported snapshot boundary, not an assertion of perpetual zero lag.

Additional explicit phases (never automatically chained after failure):

- `upgrade_candidate`: before **each** reviewed embedded SQL migration, encrypt
  a candidate checkpoint, restore it into another new isolated database and
  compare every business-row fingerprint. Run checksum-pinned `migratectl`
  in a read-only disposable client container. Verify exact clean versions and
  unchanged protected data. Never modify the active `account` database here.
- `activate`: require the upgraded candidate OID and fingerprints. Read and
  validate the nine approved existing V3 Stripe test prices against Stripe's
  API (no purchases). Remove source-generation live price bindings from the
  candidate catalog only; retain V3 Sandbox mappings and approved catalog terms.
  Copy the existing non-superuser `account_user` role's object-level permissions without
  changing role passwords/attributes/default grants. Rename the old `account`
  to `account_before_<run>` and the verified candidate to `account` in one
  transaction. No database is dropped, and old/candidate OIDs are verified.

Resume and runtime acceptance remain separate explicit owner operations. The
old database, per-step checkpoints and verification databases are retained.
Do not claim password-login, proxy connectivity or real-payment acceptance from
readiness probes. Snapshot counts describe the recorded boundary while V2 keeps
serving; they are not a claim of perpetual zero lag.

Business credentials belong to the shared canonical owner:
`kv/prod/databases` key `account_pg_password`, rendered in
`/etc/xcontrol/web-saas-recovery/business-database.env`. Refresh never generates,
rotates or injects a business password and preserves the existing role attributes,
including the reviewed RLS contract. Keep retained databases and receipts for
recovery; do not recreate a business role to retry a failed refresh.

Local verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 roles/web_saas_generation_refresh/files/test_refresh.py
RUN_POSTGRES_TESTS=1 ACCOUNTS_TEST_MIGRATECTL=/path/to/verified/linux/migratectl \
  PYTHONDONTWRITEBYTECODE=1 python3 roles/web_saas_generation_refresh/files/test_refresh_postgres.py
ansible-playbook --syntax-check -i localhost, web-saas-generation-refresh.yml
```

The PostgreSQL test uses a disposable network-isolated PostgreSQL 17 fixture;
with the verified Linux binary it executes both real embedded catalog SQL steps,
their backup/restore gates and database rename. No live test account is created.
