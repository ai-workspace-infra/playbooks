#!/usr/bin/env bash
# One-time initialization backup only. No schema version adoption or qualification.
set -euo pipefail
umask 077
read -r INITIALIZATION_BACKUP_PASSPHRASE
export INITIALIZATION_BACKUP_PASSPHRASE
[[ ${#INITIALIZATION_BACKUP_PASSPHRASE} -ge 32 ]]
[[ $(findmnt -n -o TARGET --target /data) == /data ]]
[[ $(findmnt -n -o FSTYPE --target /data) == ext4 ]]
[[ $(readlink -f "$(findmnt -n -o SOURCE --target /data)") == $(readlink -f /dev/disk/by-id/google-web-saas-uat-upgrade-data) ]]
parent=/data/backups/web-saas/uat/initialization
[[ $(realpath -m "$parent") == "$parent" ]]
install -d -m 700 -o root -g root "$parent"
checkpoint=$(mktemp -d "$parent/checkpoint.XXXXXXXXXXXX")
archive="$checkpoint/account.dump.enc"
pg=web-saas-postgresql
restore="release_initialization_verify_$(tr -d '-' </proc/sys/kernel/random/uuid)"
created=false
restore_oid=''
q() { docker exec -e PGTZ=UTC "$pg" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 -c "$2"; }
cleanup() {
 if [[ $created == true && $(q postgres "SELECT oid FROM pg_database WHERE datname='$restore'") == "$restore_oid" ]]; then
  docker exec "$pg" psql -U postgres -d postgres -Xq -v ON_ERROR_STOP=1 -c "DROP DATABASE $restore"
 fi
}
trap cleanup EXIT
schema_fingerprint() {
 docker exec "$pg" pg_dump -U postgres -d "$1" --schema-only --schema=public --no-owner --no-privileges \
  | sed '/^\\restrict /d; /^\\unrestrict /d' | sha256sum | awk '{print $1}'
}
data_fingerprint() {
 local commands
 commands=$(q "$1" "SELECT statement FROM (SELECT format('SELECT %L; SELECT to_jsonb(t)::text FROM %I.%I t ORDER BY (to_jsonb(t)::text) COLLATE \"C\";', 'table:public.' || c.relname, 'public', c.relname) statement FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p','m') UNION ALL SELECT format('SELECT %L || last_value::text || %L || is_called::text FROM %I.%I;', 'sequence:public.' || c.relname || ':', ':', 'public', c.relname) FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind='S') commands ORDER BY statement COLLATE \"C\"")
 { printf '%s\n' "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" "SET LOCAL TIMEZONE='UTC';" "SET LOCAL DATESTYLE='ISO,YMD';" "SET LOCAL extra_float_digits=3;" "$commands" "COMMIT;"; } \
  | docker exec -i "$pg" psql -U postgres -d "$1" -XAtq -v ON_ERROR_STOP=1 | sha256sum | awk '{print $1}'
}
source_schema=$(schema_fingerprint account)
source_data=$(data_fingerprint account)
source_version=$(q account "SELECT coalesce(to_regclass('public.schema_migrations')::text,'absent')")
source_users=$(q account 'SELECT count(*) FROM public.users')
source_subscriptions=$(q account 'SELECT count(*) FROM public.subscriptions')
source_tables=$(q account "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','m')")
docker exec "$pg" pg_dump -U postgres -d account --format=custom --schema=public --no-owner --no-privileges \
 | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:INITIALIZATION_BACKUP_PASSPHRASE > "$archive"
test -s "$archive"
sync
# A changed live source blocks acceptance; never assert that a mixed snapshot passed.
[[ $(schema_fingerprint account) == "$source_schema" && $(data_fingerprint account) == "$source_data" ]]
[[ $(q postgres "SELECT count(*) FROM pg_database WHERE datname='$restore'") == 0 ]]
docker exec "$pg" psql -U postgres -d postgres -Xq -v ON_ERROR_STOP=1 -c "CREATE DATABASE $restore TEMPLATE template0"
created=true
restore_oid=$(q postgres "SELECT oid FROM pg_database WHERE datname='$restore'")
[[ $restore_oid =~ ^[1-9][0-9]*$ ]]
# DROP is limited to an empty schema in the freshly created, OID-bound database.
docker exec "$pg" psql -U postgres -d "$restore" -Xq -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:INITIALIZATION_BACKUP_PASSPHRASE -in "$archive" \
 | docker exec -i "$pg" pg_restore -U postgres -d "$restore" --no-owner --no-privileges --exit-on-error
[[ $(schema_fingerprint "$restore") == "$source_schema" && $(data_fingerprint "$restore") == "$source_data" ]]
[[ $(q "$restore" "SELECT coalesce(to_regclass('public.schema_migrations')::text,'absent')") == "$source_version" ]]
[[ $(q "$restore" 'SELECT count(*) FROM users') == "$source_users" && $(q "$restore" 'SELECT count(*) FROM subscriptions') == "$source_subscriptions" ]]
[[ $(schema_fingerprint account) == "$source_schema" && $(data_fingerprint account) == "$source_data" ]]
archive_sha=$(sha256sum "$archive" | awk '{print $1}')
python3 - "$archive" "$archive_sha" "$source_schema" "$source_data" "$source_tables" "$source_users" "$source_subscriptions" "$source_version" <<'PY'
import json,sys
path,archive,schema,data,tables,users,subscriptions,ledger=sys.argv[1:]
receipt={'schema':'uat-initialization-backup/v1','environment':'uat','backend':'selfhost',
 'archive_path':path,'archive_sha256':archive,'schema_sha256':schema,'data_sha256':data,
 'public_table_count':int(tables),'users':int(users),'subscriptions':int(subscriptions),
 'migration_ledger':ledger,'encrypted':True,'durable_mount':'/data',
 'isolated_restore_verified':True,'source_restore_schema_matches':True,
 'restored_data_matches':True,'live_source_unchanged':True,'business_acceptance':False,'success':True}
with open(path.rsplit('/',1)[0]+'/receipt.json','x') as f:json.dump(receipt,f,sort_keys=True)
print(json.dumps(receipt,sort_keys=True))
PY
