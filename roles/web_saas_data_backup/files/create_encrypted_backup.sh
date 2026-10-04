#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() { printf '%s\n' "$1" >&2; exit 2; }
require() { [[ -n "${!1:-}" ]] || fail "missing required input: $1"; }
for name in WEB_SAAS_BACKUP_ROOT WEB_SAAS_DATABASE WEB_SAAS_POSTGRES_CONTAINER WEB_SAAS_ENVIRONMENT WEB_SAAS_RUN_ID WEB_SAAS_EXPECTED_VERSION WEB_SAAS_SOURCE_DATABASE_ID WEB_SAAS_BASELINE_ID WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID WEB_SAAS_BACKUP_PASSPHRASE; do require "$name"; done

[[ "$WEB_SAAS_DATABASE" == account ]] || fail 'only the account database is permitted'
[[ "$WEB_SAAS_POSTGRES_CONTAINER" == web-saas-postgresql ]] || fail 'unexpected PostgreSQL container'
[[ "$WEB_SAAS_ENVIRONMENT" =~ ^(uat|prod)$ ]] || fail 'invalid environment'
[[ "$WEB_SAAS_RUN_ID" =~ ^[1-9][0-9]*$ ]] || fail 'invalid run id'
[[ "$WEB_SAAS_EXPECTED_VERSION" =~ ^[1-9][0-9]*$ ]] || fail 'invalid exact schema version'
for value in "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID"; do
  [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$ ]] || fail 'invalid source, baseline, or sample reference'
done
[[ ${#WEB_SAAS_BACKUP_PASSPHRASE} -ge 32 ]] || fail 'encryption input is too short'
[[ "$WEB_SAAS_BACKUP_ROOT" =~ ^/data/backups/web-saas/${WEB_SAAS_ENVIRONMENT}/[A-Za-z0-9][A-Za-z0-9._-]*/${WEB_SAAS_RUN_ID}$ ]] || fail 'checkpoint must be beneath the mounted /data backup root for this environment and run'

archive="$WEB_SAAS_BACKUP_ROOT/account.dump.enc"
[[ ! -e "$archive" && ! -L "$archive" ]] || fail 'archive already exists; refusing overwrite'

psql_value() {
  docker exec "$WEB_SAAS_POSTGRES_CONTAINER" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 -c "$2"
}

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
source_state="$(psql_value "$WEB_SAAS_DATABASE" "SELECT (SELECT count(*) FROM public.schema_migrations)::text || ':' || (SELECT version::text || ':' || dirty::text FROM public.schema_migrations LIMIT 1) || ',' || (SELECT count(*) FROM public.users)::text || ',' || (SELECT count(*) FROM public.subscriptions)::text")" || fail 'could not read source database state'
IFS=',' read -r migration_state user_count subscription_count <<<"$source_state"
[[ "$migration_state" == "1:${WEB_SAAS_EXPECTED_VERSION}:false" ]] || fail 'source migration state is missing, dirty, duplicated, or not the exact target'
[[ "$user_count" =~ ^[1-9][0-9]*$ ]] || fail 'source user sample is empty'
[[ "$subscription_count" =~ ^[1-9][0-9]*$ ]] || fail 'authorized nonempty subscription sample is not present'
source_schema="$(docker exec "$WEB_SAAS_POSTGRES_CONTAINER" pg_dump -U postgres -d "$WEB_SAAS_DATABASE" --schema-only --schema=public --no-owner --no-privileges | sed '/^\\restrict /d; /^\\unrestrict /d' | sha256sum | awk '{print $1}')" || fail 'could not fingerprint source schema'
[[ "$source_schema" =~ ^[0-9a-f]{64}$ ]] || fail 'source schema fingerprint is invalid'
source_data="$(data_fingerprint "$WEB_SAAS_DATABASE")" || fail 'could not fingerprint source rows and sequences'
[[ "$source_data" =~ ^[0-9a-f]{64}$ ]] || fail 'source data fingerprint is invalid'
database_system_identifier="$(psql_value "$WEB_SAAS_DATABASE" 'SELECT system_identifier::text FROM pg_control_system()')" || fail 'could not read PostgreSQL system identity'
[[ "$database_system_identifier" =~ ^[0-9]+$ ]] || fail 'PostgreSQL system identity is invalid'

pending="$(mktemp "$WEB_SAAS_BACKUP_ROOT/.account.dump.enc.pending.XXXXXX")"
trap 'rm -f -- "$pending"' EXIT
chmod 0600 "$pending"
docker exec "$WEB_SAAS_POSTGRES_CONTAINER" pg_dump -U postgres -d "$WEB_SAAS_DATABASE" -Fc --schema=public --no-owner --no-privileges \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:WEB_SAAS_BACKUP_PASSPHRASE >"$pending"
[[ -s "$pending" ]] || fail 'encrypted archive is empty'
mv -n -- "$pending" "$archive"
[[ ! -e "$pending" ]] || fail 'archive destination appeared concurrently; pending file was not published'
trap - EXIT
chmod 0600 "$archive"
archive_sha256="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'archive checksum is invalid'
sync -f "$archive"
sync -f "$WEB_SAAS_BACKUP_ROOT"

python3 - "$WEB_SAAS_BACKUP_ROOT" "$WEB_SAAS_ENVIRONMENT" "$WEB_SAAS_RUN_ID" "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_BASELINE_ID" "$WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID" "$WEB_SAAS_EXPECTED_VERSION" "$user_count" "$subscription_count" "$source_schema" "$archive_sha256" "$database_system_identifier" "$source_data" <<'PY'
import json
import os
import sys

(root, environment, run_id, source_id, baseline_id, sample_id, version,
 users, subscriptions, schema_sha256, archive_sha256, system_identifier, data_sha256) = sys.argv[1:]
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
    "existing_users": int(users),
    "subscriptions": int(subscriptions),
    "schema_sha256": schema_sha256,
    "data_sha256": data_sha256,
    "database_system_identifier": system_identifier,
    "archive_path": os.path.join(root, "account.dump.enc"),
    "archive_sha256": archive_sha256,
    "encrypted": True,
    "durable": True,
    "business_acceptance": False,
}, sort_keys=True))
PY
