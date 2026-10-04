#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 2; }

[[ "${VAULT_ENV_PATH:-}" == "uat" ]] || fail "Accounts incremental schema migration is UAT-only."
[[ "${RELEASE_CHECKPOINT_VERIFIED:-false}" == "true" ]] || fail "A verified, durable encrypted UAT checkpoint is required before migration."
snapshot_tag="${SNAPSHOT_TAG:-}"
[[ "${snapshot_tag}" =~ ^(uat-)?daily-build-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-r[1-9][0-9]*$ ]] || fail "An immutable UAT snapshot tag is required."

accounts_dir="${ACCOUNTS_DIR:-}"
[[ -d "${accounts_dir}/sql/migrations" && -f "${accounts_dir}/go.mod" ]] || fail "Accounts migration source is missing."
target_dsn="${TARGET_DSN:-}"
project_ref="${PROJECT_REF:-}"
[[ "${project_ref}" =~ ^[a-z0-9]{20}$ ]] || fail "Vault PROJECT_REF is missing or invalid."
command -v python3 >/dev/null || fail "Python 3 is required to validate the target connection."
if ! target_dsn="$(TARGET_DSN="${target_dsn}" PROJECT_REF="${project_ref}" python3 "$(dirname "${BASH_SOURCE[0]}")/normalize_accounts_uat_dsn.py")"; then
  fail "Target connection does not match the UAT Supabase session pooler project."
fi

expected="${ACCOUNTS_SCHEMA_EXPECTED_VERSION:-}"
target="${ACCOUNTS_SCHEMA_TARGET_VERSION:-}"
expected_sha="${ACCOUNTS_SCHEMA_SHA256:-}"
[[ "${expected}" =~ ^[0-9]+$ && "${target}" =~ ^[0-9]+$ && "${target}" -gt "${expected}" ]] || fail "Expected and target versions must be increasing numbers."
[[ "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || fail "A lowercase SHA-256 digest is required."
command -v psql >/dev/null || fail "psql is required."
command -v go >/dev/null || fail "Go is required to run Accounts migratectl."

shopt -s nullglob
migration_files=("${accounts_dir}/sql/migrations/${target}_"*.up.sql)
[[ ${#migration_files[@]} -eq 1 ]] || fail "Exactly one target .up.sql migration must exist in the snapshot."
migration_file="${migration_files[0]}"
if command -v sha256sum >/dev/null; then
  actual_sha="$(sha256sum "${migration_file}" | awk '{print $1}')"
elif command -v shasum >/dev/null; then
  actual_sha="$(shasum -a 256 "${migration_file}" | awk '{print $1}')"
else
  fail "A SHA-256 utility is required."
fi
[[ "${actual_sha}" == "${expected_sha}" ]] || fail "Target migration checksum differs from the reviewed digest."

# The reviewed finance migration replaces protection triggers, not data. Keep
# the destructive-statement guard for every other payload; this exception is
# bound to the entire immutable file, not a permissive DROP TRIGGER regexp.
reviewed_finance_sha=d066e223641b4eccbb65a00dce70f717b6dce02491d1d54edc1099baf2071433
if [[ "${target}" == 2026092801 && "${actual_sha}" == "${reviewed_finance_sha}" ]]; then
  :
elif grep -Eiq '^[[:space:]]*(DROP|TRUNCATE|DELETE|UPDATE)[[:space:]]' "${migration_file}"; then
  fail "The selected migration contains a top-level destructive or data-rewriting statement."
fi

pending=0
for file in "${accounts_dir}"/sql/migrations/*.up.sql; do
  name="${file##*/}"
  version="${name%%_*}"
  [[ "${version}" =~ ^[0-9]+$ ]] || fail "Invalid migration filename in the selected Accounts tag."
  if (( 10#${version} > 10#${expected} )); then
    ((pending += 1))
    [[ "${version}" == "${target}" ]] || fail "The selected tag contains another pending migration; deploy separately."
  fi
done
[[ ${pending} -eq 1 ]] || fail "The selected tag must contain exactly one pending migration."

schema_probe_sql="SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name IN ('subscription_valid_from','subscription_valid_until','last_active_at','archived_at'))::text || ':' || (SELECT count(*) FROM pg_constraint WHERE conrelid='public.users'::regclass AND conname='users_subscription_validity_order_ck' AND convalidated)::text || ':' || (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('overlay_registrations_owner_created_idx','overlay_registrations_network_pending_idx','overlay_registrations_network_created_idx','overlay_registrations_identity_pending_idx') AND i.indisvalid AND i.indisready)::text"
schema_probe() {
  local result
  result="$(psql "${target_dsn}" -X -v ON_ERROR_STOP=1 -Atqc "${schema_probe_sql}" 2>/dev/null)" || fail "Could not verify required UAT Accounts schema prerequisites."
  [[ "${result}" == "4:1:4" ]] || fail "Required UAT subscription columns, validity constraint, or baseline indexes are missing or invalid."
}
data_sentinel() {
  local result
  local -a lines
  result="$(TARGET_DSN="${target_dsn}" bash "$(dirname "${BASH_SOURCE[0]}")/accounts_uat_data_sentinel.sh")" || fail "Could not calculate the private UAT user/subscription sentinel."
  mapfile -t lines <<<"${result}"
  [[ ${#lines[@]} -eq 2 && "${lines[0]}" =~ ^users:[0-9]+:[0-9a-f]{64}$ && "${lines[1]}" =~ ^subscriptions:[0-9]+:[0-9a-f]{64}$ ]] || fail "UAT user/subscription sentinel result is invalid."
  printf '%s' "${result}"
}

version_sql="SELECT version::text || ':' || dirty::text FROM public.schema_migrations"
current="$(psql "${target_dsn}" -X -v ON_ERROR_STOP=1 -Atqc "${version_sql}" 2>/dev/null)" || fail "Could not read UAT schema_migrations."
[[ "${current}" == "${expected}:false" || "${current}" == "${target}:false" ]] || fail "UAT schema version/dirty state differs from the reviewed precondition."
schema_probe
sentinel_before="$(data_sentinel)"

echo "Applying reviewed Accounts migration ${target} from ${snapshot_tag} (sha256 ${actual_sha}) to UAT."
if ! (cd "${accounts_dir}" && go run ./cmd/migratectl migrate --dsn "${target_dsn}" --dir sql/migrations) >/dev/null 2>&1; then
  fail "Accounts migration command failed; downstream deployment is blocked."
fi

after="$(psql "${target_dsn}" -X -v ON_ERROR_STOP=1 -Atqc "${version_sql}" 2>/dev/null)" || fail "Could not verify UAT schema_migrations after apply."
[[ "${after}" == "${target}:false" ]] || fail "UAT schema did not reach the expected clean target version."
schema_probe
sentinel_after="$(data_sentinel)"
[[ "${sentinel_after}" == "${sentinel_before}" ]] || fail "Existing UAT user/subscription sentinel changed during migration; downstream deployment is blocked."

# Exercise the official migrator again, rather than inferring idempotence from
# IF NOT EXISTS clauses or a clean version number.
if ! (cd "${accounts_dir}" && go run ./cmd/migratectl migrate --dsn "${target_dsn}" --dir sql/migrations) >/dev/null 2>&1; then
  fail "Repeated Accounts migration failed; downstream deployment is blocked."
fi
after_repeat="$(psql "${target_dsn}" -X -v ON_ERROR_STOP=1 -Atqc "${version_sql}" 2>/dev/null)" || fail "Could not verify repeated UAT migration."
[[ "${after_repeat}" == "${target}:false" ]] || fail "Repeated migration changed the clean target version."
schema_probe
sentinel_repeat="$(data_sentinel)"
[[ "${sentinel_repeat}" == "${sentinel_before}" ]] || fail "Repeated migration changed existing UAT data."
users_count="$(sed -n 's/^users:\([0-9][0-9]*\):.*/\1/p' <<<"${sentinel_before}")"
subscriptions_count="$(sed -n 's/^subscriptions:\([0-9][0-9]*\):.*/\1/p' <<<"${sentinel_before}")"
echo "Verified UAT migration version, repeated official migration, required schema probes, and unchanged user/subscription sentinel (${users_count} users, ${subscriptions_count} subscriptions)."
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'official_migrator_repeat_verified=true\n' >>"${GITHUB_OUTPUT}"
fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf 'Accounts UAT schema migration: `%s` → `%s`, snapshot `%s`, migration SHA-256 `%s`; required schema probes passed and user/subscription sentinel was unchanged (%s users, %s subscriptions).\n' "${expected}" "${target}" "${snapshot_tag}" "${actual_sha}" "${users_count}" "${subscriptions_count}" >>"${GITHUB_STEP_SUMMARY}"
fi
