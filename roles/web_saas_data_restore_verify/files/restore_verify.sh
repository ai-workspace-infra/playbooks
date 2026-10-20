#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() { [[ -s "$diagnostic_path" ]] || write_diagnostic "restore_verify_failed:${phase}:$1"; printf '%s\n' "$1" >&2; exit 2; }
require() { [[ -n "${!1:-}" ]] || fail "missing required input: $1"; }
diagnostic_path="${WEB_SAAS_DIAGNOSTIC_PATH:-}"
phase='preflight'
write_diagnostic() {
  [[ -n "$diagnostic_path" ]] || return 0
  umask 077
  printf '%s\n' "$1" >"$diagnostic_path"
  chmod 0600 "$diagnostic_path"
}
if [[ ${WEB_SAAS_BACKUP_KEY_STDIN:-false} == true ]]; then
  IFS= read -r WEB_SAAS_BACKUP_PASSPHRASE || fail 'missing stdin encryption key'
  export WEB_SAAS_BACKUP_PASSPHRASE
fi
for name in WEB_SAAS_ARCHIVE_PATH WEB_SAAS_DATABASE WEB_SAAS_POSTGRES_CONTAINER WEB_SAAS_ENVIRONMENT WEB_SAAS_RUN_ID WEB_SAAS_EXPECTED_VERSION WEB_SAAS_SOURCE_DATABASE_ID WEB_SAAS_BASELINE_ID WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID WEB_SAAS_EXPECTED_USERS WEB_SAAS_EXPECTED_SUBSCRIPTIONS WEB_SAAS_EXPECTED_SCHEMA_SHA256 WEB_SAAS_EXPECTED_DATA_SHA256 WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER WEB_SAAS_ARCHIVE_SHA256 WEB_SAAS_BACKUP_PASSPHRASE; do require "$name"; done

WEB_SAAS_MIGRATION_TABLE="${WEB_SAAS_MIGRATION_TABLE-schema_migrations}"
WEB_SAAS_MIGRATION_VERSION_COLUMN="${WEB_SAAS_MIGRATION_VERSION_COLUMN-version}"
WEB_SAAS_MIGRATION_DIRTY_COLUMN="${WEB_SAAS_MIGRATION_DIRTY_COLUMN-dirty}"
WEB_SAAS_MIGRATION_ORDER_COLUMN="${WEB_SAAS_MIGRATION_ORDER_COLUMN-version}"
WEB_SAAS_EXPECTED_MIGRATION_ROWS="${WEB_SAAS_EXPECTED_MIGRATION_ROWS:-1}"
WEB_SAAS_EXPECTED_MIGRATION_DIRTY="${WEB_SAAS_EXPECTED_MIGRATION_DIRTY:-false}"
WEB_SAAS_USERS_TABLE="${WEB_SAAS_USERS_TABLE-users}"
WEB_SAAS_SUBSCRIPTION_TABLE="${WEB_SAAS_SUBSCRIPTION_TABLE-subscriptions}"
WEB_SAAS_CROSS_MACHINE="${WEB_SAAS_CROSS_MACHINE-false}"
WEB_SAAS_RETAIN_RESTORE="${WEB_SAAS_RETAIN_RESTORE-false}"

[[ "$WEB_SAAS_DATABASE" == account ]] || fail 'only account is an allowed source database'
[[ "$WEB_SAAS_POSTGRES_CONTAINER" == web-saas-postgresql ]] || fail 'unexpected PostgreSQL container'
[[ "$WEB_SAAS_ENVIRONMENT" =~ ^(uat|prod|prod-v[1-9][0-9]*)$ ]] || fail 'invalid environment'
[[ "$WEB_SAAS_RUN_ID" =~ ^[1-9][0-9]*$ ]] || fail 'invalid run id'
[[ "$WEB_SAAS_EXPECTED_VERSION" =~ ^[1-9][0-9]*$ ]] || fail 'invalid exact schema version'
[[ "$WEB_SAAS_EXPECTED_USERS" =~ ^[1-9][0-9]*$ ]] || fail 'empty or invalid user sample'
[[ "$WEB_SAAS_EXPECTED_SUBSCRIPTIONS" =~ ^[0-9]+$ ]] || fail 'invalid subscription count'
[[ "$WEB_SAAS_EXPECTED_SCHEMA_SHA256" =~ ^[0-9a-f]{64}$ && "$WEB_SAAS_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid schema or archive fingerprint'
[[ "$WEB_SAAS_EXPECTED_DATA_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid data fingerprint'
[[ "$WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER" =~ ^[0-9]+$ ]] || fail 'invalid PostgreSQL system identity'
for identifier in "$WEB_SAAS_MIGRATION_TABLE" "$WEB_SAAS_MIGRATION_VERSION_COLUMN" "$WEB_SAAS_MIGRATION_ORDER_COLUMN" "$WEB_SAAS_USERS_TABLE" "$WEB_SAAS_SUBSCRIPTION_TABLE"; do
  [[ "$identifier" =~ ^[a-z_][a-z0-9_]*$ ]] || fail 'unsafe database identifier'
done
[[ -z "${WEB_SAAS_MIGRATION_DIRTY_COLUMN:-}" || "$WEB_SAAS_MIGRATION_DIRTY_COLUMN" =~ ^[a-z_][a-z0-9_]*$ ]] || fail 'unsafe migration dirty identifier'
[[ "$WEB_SAAS_EXPECTED_MIGRATION_ROWS" =~ ^[1-9][0-9]*$ ]] || fail 'invalid expected migration row count'
[[ "${WEB_SAAS_EXPECTED_MIGRATION_DIRTY:-}" =~ ^(false|not-applicable)$ ]] || fail 'invalid expected migration dirty state'
[[ "${WEB_SAAS_CROSS_MACHINE:-false}" =~ ^(true|false)$ ]] || fail 'invalid cross-machine mode'
[[ "$WEB_SAAS_RETAIN_RESTORE" =~ ^(true|false)$ ]] || fail 'invalid restore retention mode'
for value in "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID"; do
  [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$ ]] || fail 'invalid source, baseline, or sample reference'
done
[[ ${#WEB_SAAS_BACKUP_PASSPHRASE} -ge 32 ]] || fail 'encryption input is too short'
[[ "$WEB_SAAS_ARCHIVE_PATH" =~ ^/data/backups/web-saas/${WEB_SAAS_ENVIRONMENT}/[A-Za-z0-9][A-Za-z0-9._-]*/${WEB_SAAS_RUN_ID}/account\.dump\.enc$ ]] || fail 'archive path does not belong to this environment and run'
[[ -f "$WEB_SAAS_ARCHIVE_PATH" && ! -L "$WEB_SAAS_ARCHIVE_PATH" ]] || fail 'archive is missing or a symbolic link'
actual_archive_sha256="$(sha256sum "$WEB_SAAS_ARCHIVE_PATH" | awk '{print $1}')"
[[ "$actual_archive_sha256" == "$WEB_SAAS_ARCHIVE_SHA256" ]] || fail 'archive checksum changed'

restore_database="release_verify_${WEB_SAAS_RUN_ID}"
restore_created=0
restore_oid=''
restore_verified=0
psql_value() { docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 -c "$2"; }

# Hash all public table rows and sequence states without emitting row values or
# writing a plaintext file. Use a read-only repeatable-read snapshot and stable
# serialization settings; concurrent writes cause restore verification to block.
data_fingerprint() {
  local commands
  commands="$(psql_value "$1" "SELECT statement FROM (SELECT format('SELECT %L; SELECT to_jsonb(t)::text FROM %I.%I t ORDER BY (to_jsonb(t)::text) COLLATE \"C\";', 'table:public.' || c.relname, 'public', c.relname) AS statement FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','m') UNION ALL SELECT format('SELECT %L || last_value::text || %L || is_called::text FROM %I.%I;', 'sequence:public.' || c.relname || ':', ':', 'public', c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S' ) statements ORDER BY statement COLLATE \"C\"")" || return
  {
    printf "%s\n" "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" "SET LOCAL TIMEZONE='UTC';" "SET LOCAL DATESTYLE='ISO,YMD';" "SET LOCAL extra_float_digits=3;"
    printf '%s\n' "$commands" "COMMIT;"
  } | docker exec -i "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 | sha256sum | awk '{print $1}'
}
state_value() {
  if [[ -n "${WEB_SAAS_MIGRATION_DIRTY_COLUMN:-}" ]]; then
    migration_dirty_sql="(SELECT COALESCE(bool_or(${WEB_SAAS_MIGRATION_DIRTY_COLUMN}), false)::text FROM public.${WEB_SAAS_MIGRATION_TABLE})"
  else
    migration_dirty_sql="'not-applicable'"
  fi
  psql_value "$1" "SELECT (SELECT count(*) FROM public.${WEB_SAAS_MIGRATION_TABLE})::text || ':' || (SELECT ${WEB_SAAS_MIGRATION_VERSION_COLUMN}::text FROM public.${WEB_SAAS_MIGRATION_TABLE} ORDER BY ${WEB_SAAS_MIGRATION_ORDER_COLUMN} DESC LIMIT 1) || ':' || ${migration_dirty_sql} || ',' || (SELECT count(*) FROM public.${WEB_SAAS_USERS_TABLE})::text || ',' || (SELECT count(*) FROM public.${WEB_SAAS_SUBSCRIPTION_TABLE})::text"
}
schema_fingerprint() {
  docker exec "$WEB_SAAS_POSTGRES_CONTAINER" pg_dump -U postgres -d "$1" --schema-only --schema=public --no-owner --no-privileges | sed '/^\\restrict /d; /^\\unrestrict /d' | sha256sum | awk '{print $1}'
}
cleanup() {
  local exit_status=$?
  local current_oid
  trap - EXIT
  if (( exit_status != 0 )) && [[ ! -s "$diagnostic_path" ]]; then
    write_diagnostic "restore_verify_failed:${phase}:exit_${exit_status}" || true
  fi
  if [[ "$restore_created" == 1 && -n "$restore_oid" && !( "$WEB_SAAS_RETAIN_RESTORE" == true && "$exit_status" == 0 && "$restore_verified" == 1 ) ]]; then
    current_oid="$(psql_value postgres "SELECT oid FROM pg_database WHERE datname = '${restore_database}'")" || { write_diagnostic 'restore_verify_failed:cleanup_identity_query'; return 1; }
    if [[ "$current_oid" == "$restore_oid" ]]; then
      docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d postgres -Xq -v ON_ERROR_STOP=1 -c "DROP DATABASE ${restore_database}" || { write_diagnostic 'restore_verify_failed:cleanup_temp_database'; return 1; }
    fi
  fi
  return "$exit_status"
}
trap cleanup EXIT

restore_exists="$(psql_value postgres "SELECT count(*) FROM pg_database WHERE datname = '${restore_database}'")" || fail 'could not inspect temporary database name'
[[ "$restore_exists" == 0 ]] || fail 'temporary database exists before this run; refusing to restore into or drop it'

if [[ "${WEB_SAAS_CROSS_MACHINE:-false}" == true ]]; then
  # A cross-machine restore must not connect to the source database. The
  # receipt is the source-of-truth and PostgreSQL system identifiers are
  # expected to differ on a new cluster.
  source_state="${WEB_SAAS_EXPECTED_MIGRATION_ROWS}:${WEB_SAAS_EXPECTED_VERSION}:${WEB_SAAS_EXPECTED_MIGRATION_DIRTY},${WEB_SAAS_EXPECTED_USERS},${WEB_SAAS_EXPECTED_SUBSCRIPTIONS}"
  user_count="$WEB_SAAS_EXPECTED_USERS"
  subscription_count="$WEB_SAAS_EXPECTED_SUBSCRIPTIONS"
  source_schema="$WEB_SAAS_EXPECTED_SCHEMA_SHA256"
  source_data="$WEB_SAAS_EXPECTED_DATA_SHA256"
  source_system_identifier="$WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER"
else
  # Recheck the live source after backup creation so the archive remains bound
  # to the same database identity, clean migration, authorized sample counts,
  # schema, and rows.
  source_state="$(state_value "$WEB_SAAS_DATABASE")" || fail 'could not re-read source database state'
  IFS=',' read -r migration_state user_count subscription_count <<<"$source_state"
  [[ "$migration_state" == "${WEB_SAAS_EXPECTED_MIGRATION_ROWS}:${WEB_SAAS_EXPECTED_VERSION}:${WEB_SAAS_EXPECTED_MIGRATION_DIRTY}" ]] || fail 'source database is no longer the exact clean schema baseline'
  [[ "$user_count" == "$WEB_SAAS_EXPECTED_USERS" && "$subscription_count" == "$WEB_SAAS_EXPECTED_SUBSCRIPTIONS" ]] || fail 'source database sample counts changed after backup'
  source_schema="$(schema_fingerprint "$WEB_SAAS_DATABASE")" || fail 'could not re-fingerprint source schema'
  [[ "$source_schema" == "$WEB_SAAS_EXPECTED_SCHEMA_SHA256" ]] || fail 'source database identity/schema changed after backup'
  source_data="$(data_fingerprint "$WEB_SAAS_DATABASE")" || fail 'could not re-fingerprint source rows'
  [[ "$source_data" == "$WEB_SAAS_EXPECTED_DATA_SHA256" ]] || fail 'source rows or sequences changed after backup; no automatic retry'
  source_system_identifier="$(psql_value "$WEB_SAAS_DATABASE" 'SELECT system_identifier::text FROM pg_control_system()')" || fail 'could not re-read PostgreSQL system identity'
  [[ "$source_system_identifier" == "$WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER" ]] || fail 'PostgreSQL source identity changed after backup'
fi

docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d postgres -Xq -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${restore_database} TEMPLATE template0"
restore_created=1
phase='record_isolated_database_identity'
restore_oid="$(psql_value postgres "SELECT oid FROM pg_database WHERE datname = '${restore_database}'")"
[[ "$restore_oid" =~ ^[0-9]+$ ]] || fail 'could not record temporary database identity'
# The public-only archive creates its schema. Remove ONLY the empty default
# schema in the brand-new, OID-bound verification database; never the source.
# No CASCADE: unexpected template contents make this fail safely.
phase='prepare_isolated_schema'
docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$restore_database" -Xq -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public'
phase='decrypt_and_restore_archive'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:WEB_SAAS_BACKUP_PASSPHRASE -in "$WEB_SAAS_ARCHIVE_PATH" \
  | docker exec -i "$WEB_SAAS_POSTGRES_CONTAINER" pg_restore -U postgres -d "$restore_database" --no-owner --no-privileges --exit-on-error

phase='compare_restored_database'
restored_state="$(state_value "$restore_database")" || fail 'could not inspect restored database'
restored_schema="$(schema_fingerprint "$restore_database")" || fail 'could not fingerprint restored schema'
restored_system_identifier="$(psql_value "$restore_database" 'SELECT system_identifier::text FROM pg_control_system()')" || fail 'could not read restored PostgreSQL system identity'
[[ "$restored_state" == "$source_state" ]] || fail 'restored database migration state or sample counts differ from source'
[[ "$restored_schema" == "$source_schema" ]] || fail 'restored database schema differs from source'
restored_data="$(data_fingerprint "$restore_database")" || fail 'could not fingerprint restored rows'
if [[ "$restored_data" != "$source_data" ]]; then
  write_diagnostic "restore_verify_failed:${phase}:data_fingerprint_mismatch:expected=${source_data}:actual=${restored_data}"
  fail 'restored full-row or sequence data differs from source'
fi
if [[ "${WEB_SAAS_CROSS_MACHINE:-false}" != true ]]; then
  [[ "$restored_system_identifier" == "$source_system_identifier" ]] || fail 'restored database is not on the expected PostgreSQL instance'
fi
write_diagnostic 'restore_verify_passed'
restore_verified=1

python3 - "$WEB_SAAS_ENVIRONMENT" "$WEB_SAAS_RUN_ID" "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID" "$WEB_SAAS_EXPECTED_VERSION" "$user_count" "$subscription_count" "$source_schema" "$WEB_SAAS_ARCHIVE_SHA256" "$source_system_identifier" "$WEB_SAAS_CROSS_MACHINE" "$WEB_SAAS_EXPECTED_MIGRATION_ROWS" "$WEB_SAAS_EXPECTED_MIGRATION_DIRTY" <<'PY'
import json
import os
import sys

(environment, run_id, source_id, baseline_id, sample_id, version, users,
 subscriptions, schema_sha256, archive_sha256, system_identifier, cross_machine,
 migration_rows, migration_dirty) = sys.argv[1:]
print(json.dumps({
    "schema": 1,
    "phase": "database_restore_verify_component",
    "status": "passed",
    "database": "account",
    "restore_database": f"release_verify_{run_id}",
    "source_database_id": source_id,
    "baseline_id": baseline_id,
    "authorized_subscription_sample_id": sample_id,
    "environment": environment,
    "schema_version": int(version),
    "migration_rows": int(migration_rows),
    "migration_dirty": migration_dirty,
    "existing_users": int(users),
    "subscriptions": int(subscriptions),
    "schema_sha256": schema_sha256,
    "data_sha256": os.environ["WEB_SAAS_EXPECTED_DATA_SHA256"],
    "database_system_identifier": system_identifier,
    "source_system_identity_comparison": "not_applicable" if cross_machine == "true" else "equal",
    "cross_machine_restore": cross_machine == "true",
    "archive_sha256": archive_sha256,
    "isolated_restore_verified": True,
    "source_restore_schema_matches": True,
    "restored_data_matches": True,
    "restore_retained": os.environ.get("WEB_SAAS_RETAIN_RESTORE", "false") == "true",
    "restore_gate_status": "passed",
    "g3_status": "passed" if int(subscriptions) > 0 else "blocked",
    "g3_reason_code": None if int(subscriptions) > 0 else "NONEMPTY_SUBSCRIPTION_SAMPLE_REQUIRED",
    "business_acceptance": False,
}, sort_keys=True))
PY
