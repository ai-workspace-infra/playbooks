#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() { printf '%s\n' "$1" >&2; exit 2; }
require() { [[ -n "${!1:-}" ]] || fail "missing required input: $1"; }

for name in WEB_SAAS_ARCHIVE_FORMAT WEB_SAAS_ARCHIVE_PATH WEB_SAAS_ARCHIVE_SHA256 \
  WEB_SAAS_ENVIRONMENT WEB_SAAS_RUN_ID WEB_SAAS_SOURCE_DATABASE_ID \
  WEB_SAAS_TARGET_DATABASE_ID WEB_SAAS_TARGET_HOST WEB_SAAS_TARGET_DSN \
  WEB_SAAS_BACKUP_PASSPHRASE WEB_SAAS_SOURCE_IDENTITY_STATUS; do
  require "$name"
done

[[ "$WEB_SAAS_ARCHIVE_FORMAT" == "supabase-public-sql-gzip-aes256-cbc-v1" ]] || fail 'unsupported checkpoint archive format'
[[ "$WEB_SAAS_ENVIRONMENT" == uat ]] || fail 'Supabase checkpoint restore is UAT-only'
[[ "$WEB_SAAS_RUN_ID" =~ ^[1-9][0-9]*$ ]] || fail 'invalid restore run id'
[[ "$WEB_SAAS_TARGET_HOST" == web-saas-uat ]] || fail 'restore target is not the canonical UAT host'
[[ "$WEB_SAAS_SOURCE_DATABASE_ID" != "$WEB_SAAS_TARGET_DATABASE_ID" ]] || fail 'source and isolated target identities must differ'
[[ "$WEB_SAAS_SOURCE_DATABASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$ ]] || fail 'invalid source database identity'
[[ "$WEB_SAAS_TARGET_DATABASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$ ]] || fail 'invalid target database identity'
[[ "$WEB_SAAS_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid immutable archive SHA-256'
[[ "$WEB_SAAS_SOURCE_IDENTITY_STATUS" == unavailable_from_historical_checkpoint ]] || fail 'historical source identity must be explicitly unavailable'
[[ ${#WEB_SAAS_BACKUP_PASSPHRASE} -ge 32 ]] || fail 'encryption input is too short'
[[ "$WEB_SAAS_ARCHIVE_PATH" =~ ^/data/backups/web-saas/uat/[A-Za-z0-9][A-Za-z0-9._-]*/[1-9][0-9]*/supabase_[A-Za-z0-9._-]+_daily-build-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-r[1-9][0-9]*\.sql\.gz\.enc$ ]] || fail 'archive path is not a UAT Supabase checkpoint path'
[[ -f "$WEB_SAAS_ARCHIVE_PATH" && ! -L "$WEB_SAAS_ARCHIVE_PATH" ]] || fail 'archive is missing or a symbolic link'
archive_mode="$(stat -c '%a' "$WEB_SAAS_ARCHIVE_PATH" 2>/dev/null || stat -f '%Lp' "$WEB_SAAS_ARCHIVE_PATH")"
[[ "$archive_mode" == 600 ]] || fail 'archive must be mode 0600'
actual_archive_sha256="$(sha256sum "$WEB_SAAS_ARCHIVE_PATH" | awk '{print $1}')"
[[ "$actual_archive_sha256" == "$WEB_SAAS_ARCHIVE_SHA256" ]] || fail 'archive checksum changed'

restore_database="release_verify_supabase_${WEB_SAAS_RUN_ID}"
workdir="$(mktemp -d /tmp/uat-supabase-restore.XXXXXX)"
chmod 700 "$workdir"
compressed_file="$workdir/checkpoint.sql.gz"
sql_file="$workdir/checkpoint.sql"
trap 'rm -f -- "$compressed_file" "$sql_file"; rmdir -- "$workdir" 2>/dev/null || true' EXIT

# The checkpoint producer uses AES-256-CBC, PBKDF2, and 100000 iterations.
# Decrypt and decompress into a private, short-lived file; never print it.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
  -pass env:WEB_SAAS_BACKUP_PASSPHRASE \
  -in "$WEB_SAAS_ARCHIVE_PATH" -out "$compressed_file" 2>/dev/null \
  || fail 'checkpoint decryption failed'
gzip -t "$compressed_file" 2>/dev/null || fail 'checkpoint gzip validation failed'
gzip -dc "$compressed_file" >"$sql_file" 2>/dev/null || fail 'checkpoint decompression failed'
[[ -s "$sql_file" ]] || fail 'checkpoint SQL stream is empty'

# Validate the expected pg_dump plain-SQL envelope and reject destructive or
# source-target control statements before psql sees the file.
python3 - "$sql_file" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
if "\x00" in text or not text.lstrip().startswith("-- PostgreSQL database dump"):
    raise SystemExit("checkpoint is not a PostgreSQL plain SQL dump")
if "CREATE TABLE" not in text or "SET " not in text:
    raise SystemExit("checkpoint SQL structure is incomplete")
for pattern in (
    r"(?im)^\s*DROP\s+",
    r"(?im)^\s*TRUNCATE\b",
    r"(?im)^\s*DELETE\s+FROM\b",
    r"(?im)^\s*UPDATE\s+",
    r"(?im)^\s*CREATE\s+DATABASE\b",
    r"(?im)^\s*ALTER\s+SYSTEM\b",
    r"(?im)^\s*\\connect\b",
):
    if re.search(pattern, text):
        raise SystemExit("checkpoint contains a disallowed destructive or cross-database statement")
PY
sql_sha256="$(sha256sum "$sql_file" | awk '{print $1}')"

psql_value() {
  psql -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c "$1"
}

[[ "$(psql_value 'SELECT current_database()')" == postgres ]] || fail 'target control connection must use the postgres database'
target_exists="$(psql_value "SELECT count(*) FROM pg_database WHERE datname='${restore_database}'")" || fail 'could not inspect isolated target'
[[ "$target_exists" == 0 ]] || fail 'isolated target database already exists; refusing reuse or overwrite'

# Create only the run-owned isolated target. Never connect to or modify the
# source database, and never delete this target automatically on failure.
psql -Xq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c "CREATE DATABASE ${restore_database} TEMPLATE template0" \
  >/dev/null 2>/dev/null || fail 'could not create isolated UAT target'
target_tables_before="$(psql -d "$restore_database" -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")" || fail 'could not inspect isolated target contents'
[[ "$target_tables_before" == 0 ]] || fail 'isolated target is not empty'

psql -d "$restore_database" -Xq -v ON_ERROR_STOP=1 --single-transaction "$WEB_SAAS_TARGET_DSN" -f "$sql_file" \
  >/dev/null 2>/dev/null || fail 'checkpoint SQL restore failed; isolated target retained for review'

required_tables="$(psql -d "$restore_database" -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','subscriptions')")" || fail 'could not verify restored schema'
[[ "$required_tables" == 2 ]] || fail 'restored schema is missing users or subscriptions'
target_users="$(psql -d "$restore_database" -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c 'SELECT count(*) FROM public.users')" || fail 'could not count restored users'
target_subscriptions="$(psql -d "$restore_database" -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c 'SELECT count(*) FROM public.subscriptions')" || fail 'could not count restored subscriptions'
target_system_identifier="$(psql -d "$restore_database" -XAtq -v ON_ERROR_STOP=1 "$WEB_SAAS_TARGET_DSN" -c 'SELECT system_identifier::text FROM pg_control_system()')" || fail 'could not capture isolated target identity'
[[ "$target_system_identifier" =~ ^[0-9]+$ ]] || fail 'isolated target identity is invalid'

python3 - "$WEB_SAAS_RUN_ID" "$WEB_SAAS_SOURCE_DATABASE_ID" "$WEB_SAAS_TARGET_DATABASE_ID" "$WEB_SAAS_ARCHIVE_SHA256" "$sql_sha256" "$restore_database" "$target_system_identifier" "$target_users" "$target_subscriptions" <<'PY'
import json
import sys

(run_id, source_id, target_id, archive_sha256, sql_sha256, database,
 system_identifier, users, subscriptions) = sys.argv[1:]
print(json.dumps({
    "schema": 1,
    "phase": "supabase_sql_gzip_restore_component",
    "status": "passed",
    "environment": "uat",
    "archive_format": "supabase-public-sql-gzip-aes256-cbc-v1",
    "archive_sha256": archive_sha256,
    "decrypted_sql_sha256": sql_sha256,
    "decrypt_verified": True,
    "gzip_verified": True,
    "sql_format_verified": True,
    "restore_gate_status": "passed",
    "isolated_restore_verified": True,
    "target_database": database,
    "target_database_id": target_id,
    "target_database_system_identifier": system_identifier,
    "restored_users": int(users),
    "restored_subscriptions": int(subscriptions),
    "required_schema_tables": ["users", "subscriptions"],
    "source_database_id": source_id,
    "source_identity_status": "unavailable_from_historical_checkpoint",
    "source_identity_bound": False,
    "row_fidelity_status": "not_verifiable_from_historical_checkpoint",
    "row_fidelity_bound": False,
    "business_acceptance": False,
}, sort_keys=True))
PY
