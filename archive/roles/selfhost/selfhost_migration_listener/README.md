# Explicit candidate migration listener owner

Caller: `scripts/control_plane/listener.py --config <GitOps declaration> --gitops-ref <40SHA> [--check]`.
Never called by ordinary installation. Only the existing owned CMDB candidate is
selected; no source/V4 deployment or connection is performed.

GitOps owns the combined Doco-CD selection and Compose service `migration`.
The Accounts digest must match the candidate pin. It runs the image-owned
`/usr/local/bin/migratectl serve --role target`, sharing Caddy's network namespace
so loopback 9097 is reachable without publishing that port. GitOps explicitly
binds the target-only persistent private environment, route, encrypted staging
and optional read-only owner-issued backup receipt. No role-generated Compose.

`CONTROL_PLANE_MIGRATION_VARS_FILE` is a protected reference-only YAML policy
with vault_addr, vault_token_env and secret_refs for TARGET_MIGRATION_DSN,
TARGET_MIGRATION_SIGNING_KEY and TARGET_MIGRATION_STAGING_KEY. Paths must be
`kv/<environment>/accounts/migration/<record>#<field>`. Dedicated database role,
different 32-byte signing/staging keys; no account_user or shared JWT fallback.
Vault resolves in delegated no_log task memory. Secrets are written only to
root-private target files `/etc/xcontrol/migration-api/{listener.env,caddy/route.caddy}`
(0700 directory, 0600 files). Required mounts survive reboot. Each deployment
re-resolves Vault and verifies the actual process uses current resolved values;
there is no ambient credential or old-file fallback.

Preview startup performs no receipt DDL. Apply requires the existing native
backup/isolated-restore producer receipt, authenticated checksums/owner proof,
actual encrypted archive and target DB identity. `initialize_receipts` is an
additional explicit operation using the dedicated receipt-owner role; it never
substitutes business/superuser credentials or silently grants privileges.
Existing Accounts transaction/domain owners enforce apply and committed retry.

The native candidate Caddy template imports the declared route before its
general read-only rule. Only exact Accounts Host plus `/api/migrations/v1` path
and explicit narrow client CIDRs can reach loopback; other clients get 403.
Target SNI/trust verification and an unauthenticated API 401 capability probe
are mandatory. Add `127.0.0.1/32` to the explicit client allowlist for
that target-local probe. No TLS bypass or source mutation.

Rollback: select the previous immutable ordinary candidate Doco target and
remove the opt-in route via a separately reviewed target operation. Never clear
staging, backup evidence or the receipt schema to retry. No live acceptance is
claimed by local fixtures.

Full recovery metadata is external: `CONTROL_PLANE_MIGRATION_RECOVERY_FILE`
contains authenticated nonsecret producer/run checksums and target-only paths;
it is never stored in GitOps. See the exact fields and bootstrap/restart
procedure in `docs/selfhost-migration-listener-contract.md`.
