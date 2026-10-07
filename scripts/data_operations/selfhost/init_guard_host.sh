#!/usr/bin/env bash
set -euo pipefail
environment="${1:?explicit environment required}"
[[ "$environment" =~ ^(uat|prod|sit)$ ]] || exit 2
container=web-saas-postgresql
state="$(docker inspect -f '{{.State.Status}}' "$container")"
if [[ "$state" != running ]]; then
  echo 'PostgreSQL is not running; initialization stopped.' >&2
  exit 1
fi
if [[ "$environment" == prod ]]; then
  [[ "$(findmnt -nro TARGET,FSTYPE --mountpoint /data)" == '/data ext4' ]]
  [[ "$(readlink -f "$(findmnt -nro SOURCE --mountpoint /data)")" == "$(readlink -f /dev/disk/by-id/google-web-saas-prod-data)" ]]
  mount="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}}:{{.Source}}{{end}}{{end}}' "$container")"
  [[ "$mount" == bind:/data/postgresql ]] || { echo 'PROD PostgreSQL is not bound to its independent disk.' >&2; exit 1; }
fi
database_present="$(docker exec "$container" psql -U postgres -d postgres -XAtq -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) FROM pg_database WHERE datname='account'")"
case "$database_present" in
  0) echo 'Account database is absent; empty-host initialization may proceed.'; exit 0 ;;
  1) ;;
  *) echo 'Could not verify database existence; initialization stopped.' >&2; exit 1 ;;
esac
# Count user relations and functions in every application schema, including
# sequences/views/foreign tables. Extension-owned objects do not count as data.
object_count="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -c "
SELECT
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
    AND c.relkind IN ('r','p','v','m','S','f')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e'))
 + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e'))
 + (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
    AND t.typtype IN ('e','d','r','m')
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_type'::regclass AND d.objid=t.oid AND d.deptype='e'))")"
if [[ "$object_count" != 0 ]]; then
  shape="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -c "
SELECT coalesce((SELECT version::text || ':' || dirty::text FROM public.schema_migrations ORDER BY version DESC LIMIT 1),'missing')
 || ':tables=' || (SELECT count(*)::text FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','system_release_checkpoints'))
 || ':shape=' || md5(coalesce((SELECT string_agg(tablename,',' ORDER BY tablename) FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','system_release_checkpoints')),''))")"
  echo "Account database has ${object_count} non-extension application objects; checkpoint/table shape=${shape}; refusing initialization." >&2
  exit 1
fi
echo 'Account database is empty; schema initialization may proceed.'
