#!/usr/bin/env bash
set -euo pipefail
umask 077

phase='preflight'
diagnostic_path="${WEB_SAAS_DIAGNOSTIC_PATH:-}"
write_diagnostic() {
  [[ -n "$diagnostic_path" ]] || return 0
  umask 077
  printf '%s\n' "$1" >"$diagnostic_path"
  chmod 0600 "$diagnostic_path"
}
fail() { [[ -s "$diagnostic_path" ]] || write_diagnostic "backup_failed:${phase}:$1"; printf '%s\n' "$1" >&2; exit 2; }
require() { [[ -n "${!1:-}" ]] || fail "missing required input: $1"; }
if [[ ${WEB_SAAS_BACKUP_KEY_STDIN:-false} == true ]]; then
  IFS= read -r WEB_SAAS_BACKUP_PASSPHRASE || fail 'missing stdin encryption key'
  export WEB_SAAS_BACKUP_PASSPHRASE
fi
for name in WEB_SAAS_BACKUP_ROOT WEB_SAAS_DATABASE WEB_SAAS_POSTGRES_CONTAINER WEB_SAAS_ENVIRONMENT WEB_SAAS_RUN_ID WEB_SAAS_EXPECTED_VERSION WEB_SAAS_SOURCE_DATABASE_ID WEB_SAAS_BASELINE_ID WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID WEB_SAAS_BACKUP_PASSPHRASE; do require "$name"; done

WEB_SAAS_MIGRATION_TABLE="${WEB_SAAS_MIGRATION_TABLE-schema_migrations}"
WEB_SAAS_MIGRATION_VERSION_COLUMN="${WEB_SAAS_MIGRATION_VERSION_COLUMN-version}"
WEB_SAAS_MIGRATION_DIRTY_COLUMN="${WEB_SAAS_MIGRATION_DIRTY_COLUMN-dirty}"
WEB_SAAS_MIGRATION_ORDER_COLUMN="${WEB_SAAS_MIGRATION_ORDER_COLUMN-version}"
WEB_SAAS_EXPECTED_MIGRATION_ROWS="${WEB_SAAS_EXPECTED_MIGRATION_ROWS:-1}"
WEB_SAAS_USERS_TABLE="${WEB_SAAS_USERS_TABLE-users}"
WEB_SAAS_SUBSCRIPTION_TABLE="${WEB_SAAS_SUBSCRIPTION_TABLE-subscriptions}"

[[ "$WEB_SAAS_DATABASE" == account ]] || fail 'only the account database is permitted'
[[ "$WEB_SAAS_POSTGRES_CONTAINER" == web-saas-postgresql ]] || fail 'unexpected PostgreSQL container'
[[ "$WEB_SAAS_ENVIRONMENT" =~ ^(sit|uat|prod|prod-v[1-9][0-9]*)$ ]] || fail 'invalid environment'
[[ "$WEB_SAAS_RUN_ID" =~ ^[1-9][0-9]*$ ]] || fail 'invalid run id'
[[ "$WEB_SAAS_EXPECTED_VERSION" =~ ^[1-9][0-9]*$ ]] || fail 'invalid exact schema version'
for value in "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID"; do
  [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$ ]] || fail 'invalid source, baseline, or sample reference'
done
[[ ${#WEB_SAAS_BACKUP_PASSPHRASE} -ge 32 ]] || fail 'encryption input is too short'
for identifier in "$WEB_SAAS_MIGRATION_TABLE" "$WEB_SAAS_MIGRATION_VERSION_COLUMN" "$WEB_SAAS_MIGRATION_ORDER_COLUMN" "$WEB_SAAS_USERS_TABLE" "$WEB_SAAS_SUBSCRIPTION_TABLE"; do
  [[ "$identifier" =~ ^[a-z_][a-z0-9_]*$ ]] || fail 'unsafe database identifier'
done
[[ -z "${WEB_SAAS_MIGRATION_DIRTY_COLUMN:-}" || "$WEB_SAAS_MIGRATION_DIRTY_COLUMN" =~ ^[a-z_][a-z0-9_]*$ ]] || fail 'unsafe migration dirty identifier'
[[ "$WEB_SAAS_EXPECTED_MIGRATION_ROWS" =~ ^[1-9][0-9]*$ ]] || fail 'invalid expected migration row count'
[[ "$WEB_SAAS_BACKUP_ROOT" =~ ^/data/backups/web-saas/${WEB_SAAS_ENVIRONMENT}/[A-Za-z0-9][A-Za-z0-9._-]*/${WEB_SAAS_RUN_ID}$ ]] || fail 'checkpoint must be beneath the mounted /data backup root for this environment and run'

archive="$WEB_SAAS_BACKUP_ROOT/account.dump.enc"
[[ ! -e "$archive" && ! -L "$archive" ]] || fail 'archive already exists; refusing overwrite'

psql_value() {
  docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 -c "$2"
}

pending=''
snapshot_pid=''
snapshot_id=''
snapshot_read_fd=''
snapshot_write_fd=''
cleanup_backup() {
  local status=$?
  if [[ -n "$pending" && -e "$pending" ]]; then rm -f -- "$pending"; fi
  if [[ -n "$snapshot_pid" ]]; then
    printf 'ROLLBACK;\n\\q\n' >&"$snapshot_write_fd" 2>/dev/null || true
    wait "$snapshot_pid" 2>/dev/null || true
    snapshot_pid=''
  fi
  return "$status"
}
trap cleanup_backup EXIT

begin_exported_snapshot() {
  coproc WEB_SAAS_SNAPSHOT_SESSION { docker exec -i "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$WEB_SAAS_DATABASE" -XAtq -v ON_ERROR_STOP=1; }
  snapshot_pid="$WEB_SAAS_SNAPSHOT_SESSION_PID"
  snapshot_read_fd="${WEB_SAAS_SNAPSHOT_SESSION[0]}"
  snapshot_write_fd="${WEB_SAAS_SNAPSHOT_SESSION[1]}"
  printf 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n' >&"$snapshot_write_fd"
  IFS= read -r snapshot_id <&"$snapshot_read_fd" || return 1
  [[ "$snapshot_id" =~ ^[0-9A-Fa-f-]+$ ]]
}

snapshot_psql_value() {
  local database="$1" sql="$2"
  {
    printf '%s\n' 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;' "SET TRANSACTION SNAPSHOT '$snapshot_id';" "$sql;" 'COMMIT;'
  } | docker exec -i "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$database" -XAtq -v ON_ERROR_STOP=1
}

# Hash all public table rows under the same exported MVCC snapshot used by
# pg_dump. Sequence state is not MVCC-versioned, so compare it before and after
# the archive operation and fail closed if a sequence advanced during it.
data_fingerprint() {
  local commands
  commands="$(snapshot_psql_value "$1" "SELECT statement FROM (SELECT format('SELECT %L; SELECT to_jsonb(t)::text FROM %I.%I t ORDER BY (to_jsonb(t)::text) COLLATE \"C\";', 'table:public.' || c.relname, 'public', c.relname) AS statement FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','m') UNION ALL SELECT format('SELECT %L || last_value::text || %L || is_called::text FROM %I.%I;', 'sequence:public.' || c.relname || ':', ':', 'public', c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S' ) statements ORDER BY statement COLLATE \"C\"")" || return
  {
    printf '%s\n' 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;' "SET TRANSACTION SNAPSHOT '$snapshot_id';" "SET LOCAL TIMEZONE='UTC';" "SET LOCAL DATESTYLE='ISO,YMD';" 'SET LOCAL extra_float_digits=3;'
    printf '%s\n' "$commands" "COMMIT;"
  } | docker exec -i "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 | sha256sum | awk '{print $1}'
}
if [[ -n "${WEB_SAAS_MIGRATION_DIRTY_COLUMN:-}" ]]; then
  migration_dirty_sql="(SELECT COALESCE(bool_or(${WEB_SAAS_MIGRATION_DIRTY_COLUMN}), false)::text FROM public.${WEB_SAAS_MIGRATION_TABLE})"
else
  migration_dirty_sql="'not-applicable'"
fi
phase='open_exported_snapshot'
begin_exported_snapshot || fail 'could not open a consistent read-only database snapshot'
phase='read_source_state'
source_state="$(snapshot_psql_value "$WEB_SAAS_DATABASE" "SELECT (SELECT count(*) FROM public.${WEB_SAAS_MIGRATION_TABLE})::text || ':' || (SELECT ${WEB_SAAS_MIGRATION_VERSION_COLUMN}::text FROM public.${WEB_SAAS_MIGRATION_TABLE} ORDER BY ${WEB_SAAS_MIGRATION_ORDER_COLUMN} DESC LIMIT 1) || ':' || ${migration_dirty_sql} || ',' || (SELECT count(*) FROM public.${WEB_SAAS_USERS_TABLE})::text || ',' || (SELECT count(*) FROM public.${WEB_SAAS_SUBSCRIPTION_TABLE})::text")" || fail 'could not read source database state'
IFS=',' read -r migration_state user_count subscription_count <<<"$source_state"
[[ "$migration_state" == "${WEB_SAAS_EXPECTED_MIGRATION_ROWS}:${WEB_SAAS_EXPECTED_VERSION}:${WEB_SAAS_MIGRATION_DIRTY_COLUMN:+false}" || "$migration_state" == "${WEB_SAAS_EXPECTED_MIGRATION_ROWS}:${WEB_SAAS_EXPECTED_VERSION}:not-applicable" ]] || fail 'source migration state is missing, dirty, duplicated, or not the exact target'
[[ "$user_count" =~ ^[1-9][0-9]*$ || ( "${WEB_SAAS_ALLOW_EMPTY_USERS:-false}" == true && "$user_count" == 0 ) ]] || fail 'source user sample is empty'
[[ "$subscription_count" =~ ^[0-9]+$ ]] || fail 'subscription count is invalid'
phase='fingerprint_source'
schema_args=(--schema=public)
[[ "${WEB_SAAS_FULL_DATABASE:-false}" != true ]] || schema_args=()
source_schema="$(docker exec "$WEB_SAAS_POSTGRES_CONTAINER" pg_dump -U postgres -d "$WEB_SAAS_DATABASE" --snapshot="$snapshot_id" --schema-only "${schema_args[@]}" --no-owner --no-privileges | sed '/^\\restrict /d; /^\\unrestrict /d' | sha256sum | awk '{print $1}')" || fail 'could not fingerprint source schema'
[[ "$source_schema" =~ ^[0-9a-f]{64}$ ]] || fail 'source schema fingerprint is invalid'
source_data="$(data_fingerprint "$WEB_SAAS_DATABASE")" || fail 'could not fingerprint source rows and sequences'
[[ "$source_data" =~ ^[0-9a-f]{64}$ ]] || fail 'source data fingerprint is invalid'
database_system_identifier="$(psql_value "$WEB_SAAS_DATABASE" 'SELECT system_identifier::text FROM pg_control_system()')" || fail 'could not read PostgreSQL system identity'
[[ "$database_system_identifier" =~ ^[0-9]+$ ]] || fail 'PostgreSQL system identity is invalid'

phase='create_encrypted_archive'
pending="$(mktemp "$WEB_SAAS_BACKUP_ROOT/.account.dump.enc.pending.XXXXXX")"
chmod 0600 "$pending"
docker exec "$WEB_SAAS_POSTGRES_CONTAINER" pg_dump -U postgres -d "$WEB_SAAS_DATABASE" --snapshot="$snapshot_id" -Fc "${schema_args[@]}" --no-owner --no-privileges \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:WEB_SAAS_BACKUP_PASSPHRASE >"$pending"
[[ -s "$pending" ]] || fail 'encrypted archive is empty'
mv -n -- "$pending" "$archive"
[[ ! -e "$pending" ]] || fail 'archive destination appeared concurrently; pending file was not published'
chmod 0600 "$archive"
archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'archive checksum is invalid'
phase='verify_sequence_stability'
post_dump_data="$(data_fingerprint "$WEB_SAAS_DATABASE")" || fail 'could not re-fingerprint source rows after archive creation'
[[ "$post_dump_data" == "$source_data" ]] || fail 'sequence state changed during backup; encrypted artifact retained without a receipt'
phase='close_exported_snapshot'
printf 'ROLLBACK;\n\\q\n' >&"$snapshot_write_fd"
wait "$snapshot_pid" || fail 'could not close exported source snapshot'
snapshot_pid=''
phase='finalize_archive'
sync -f "$archive"
sync -f "$WEB_SAAS_BACKUP_ROOT"

python3 - "$WEB_SAAS_BACKUP_ROOT" "$WEB_SAAS_ENVIRONMENT" "$WEB_SAAS_RUN_ID" "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID" "$WEB_SAAS_EXPECTED_VERSION" "$user_count" "$subscription_count" "$source_schema" "$archive_sha256" "$database_system_identifier" "$source_data" "$WEB_SAAS_EXPECTED_MIGRATION_ROWS" "${WEB_SAAS_MIGRATION_DIRTY_COLUMN:+false}" "$WEB_SAAS_MIGRATION_TABLE" "$WEB_SAAS_USERS_TABLE" "$WEB_SAAS_SUBSCRIPTION_TABLE" <<'PY'
import json
import os
import sys

(root, environment, run_id, source_id, baseline_id, sample_id, version,
 users, subscriptions, schema_sha256, archive_sha256, system_identifier, data_sha256,
 migration_rows, migration_dirty, migration_table, users_table, subscription_table) = sys.argv[1:]
print(json.dumps({
    "schema": 1,
    "phase": "database_backup_component",
    "status": "passed",
    "database": "account",
    "source_database_id": source_id,
    "baseline_id": baseline_id,
    "authorized_subscription_sample_id": sample_id,
    "environment": environment,
    "checkpoint_id": f"{environment}_{run_id}",
    "schema_version": int(version),
    "migration_rows": int(migration_rows),
    "migration_dirty": migration_dirty or "not-applicable",
    "migration_table": migration_table,
    "users_table": users_table,
    "subscription_table": subscription_table,
    "existing_users": int(users),
    "subscriptions": int(subscriptions),
    "schema_sha256": schema_sha256,
    "data_sha256": data_sha256,
    "database_system_identifier": system_identifier,
    "archive_path": os.path.join(root, "account.dump.enc"),
    "archive_sha256": archive_sha256,
    "encrypted": True,
    "durable": True,
    "restore_gate_status": "passed",
    "g3_status": "passed" if int(subscriptions) > 0 else "blocked",
    "g3_reason_code": None if int(subscriptions) > 0 else "NONEMPTY_SUBSCRIPTION_SAMPLE_REQUIRED",
    "business_acceptance": False,
    "full_database": os.environ.get("WEB_SAAS_FULL_DATABASE", "false") == "true",
}, sort_keys=True))
PY
