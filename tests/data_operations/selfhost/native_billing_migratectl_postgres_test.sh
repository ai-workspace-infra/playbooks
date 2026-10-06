#!/usr/bin/env bash
# Disposable GitHub-hosted PostgreSQL qualification, never live acceptance.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]
[[ "${PGHOST:-}" == 127.0.0.1 && "${PGPORT:-}" == 5432 && "${PGUSER:-}" == postgres ]]
: "${ACCOUNTS_CHECKOUT:?fixed Accounts checkout required}"
: "${BILLING_CHECKOUT:?fixed Billing checkout required}"
: "${MIGRATECTL_BIN:?CI-only compiled migration tool required}"
[[ "$(git -C "$ACCOUNTS_CHECKOUT" rev-parse HEAD)" == ddee4b01778fd1d1d644a1bc936624c81ec76093 ]]
[[ "$(git -C "$BILLING_CHECKOUT" rev-parse HEAD)" == 5b7285bf49af12983027f7624d196ab3f2b1804f ]]
[[ "$(psql -d postgres -XAtq -c 'SHOW server_version_num')" =~ ^17[0-9]{4}$ ]]
[[ "$(psql -d postgres -XAtq -c "SELECT count(*) FROM pg_database WHERE datname='account'")" == 0 ]]
createdb --template=template0 account
trap 'dropdb --if-exists account' EXIT
export NATIVE_FIXTURE_DSN=postgresql://postgres:postgres@127.0.0.1:5432/account?sslmode=disable
schema_hash="$($MIGRATECTL_BIN native-schema | jq -r .schema_sha256)"
[[ "$schema_hash" == 842cef3beb98ef819dc854ecdf5f85683233641a0cd85a9156b30ad59f7e0206 ]]
"$MIGRATECTL_BIN" init --dsn-env=NATIVE_FIXTURE_DSN --environment=uat --writers-paused \
  --dry-run=false --schema-sha256="$schema_hash" > "$RUNNER_TEMP/native-billing-fixture-init.json"
migration_hash="$(jq -r .migration_sha256 "$BILLING_CHECKOUT/sql/native-finops.manifest.json")"
[[ "$migration_hash" == a7133f3ef2ea9013a055cfd1442a7488d2b837f289e0f5d9b61624d4fde9bc53 ]]
for pass in 1 2; do
  "$MIGRATECTL_BIN" --dir="$BILLING_CHECKOUT/sql/migrations" migrate \
    --dsn-env=NATIVE_FIXTURE_DSN --expected-version=2026100601 --target-version=2026100701 \
    --migration-sha256="$migration_hash" --lock-timeout=15s --statement-timeout=5m
done
[[ "$(psql -d account -XAtq -c "SELECT version::text || ':' || dirty::text FROM schema_migrations")" == 2026100701:false ]]
[[ "$(psql -d account -XAtq -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_migrations'")" == 53 ]]
[[ "$(psql -d account -XAtq -c 'SELECT count(*) FROM cloud_vendor_costs')" == 0 ]]
[[ "$(psql -d account -XAtq -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='cloud_vendor_costs'")" == 12 ]]
if "$MIGRATECTL_BIN" --dir="$BILLING_CHECKOUT/sql/migrations" migrate --dsn-env=NATIVE_FIXTURE_DSN \
  --expected-version=2026100601 --target-version=2026100701 --migration-sha256="$(printf '%064d' 0)" \
  > "$RUNNER_TEMP/native-billing-fixture-refused.log" 2>&1; then
  echo 'Unreviewed SQL digest was not refused.' >&2; exit 1
fi
[[ "$(psql -d account -XAtq -c "SELECT version::text || ':' || dirty::text FROM schema_migrations")" == 2026100701:false ]]
echo 'Disposable PostgreSQL 17: native Accounts init plus bounded Billing schema, idempotence and wrong digest refusal passed; not production acceptance.'
