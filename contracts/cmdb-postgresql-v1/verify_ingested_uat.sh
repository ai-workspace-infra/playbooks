#!/usr/bin/env bash
# Owner: Playbooks. Read/write tests target only the explicit CMDB UAT engine.
set -euo pipefail
[[ $# == 3 ]] || { echo 'usage: verify_ingested_uat.sh RUNTIME_DIR CONTRACT_RELEASE_DIR DOCKER_NETWORK' >&2; exit 64; }
task_runtime_dir=$1
task_release_dir=$2
task_network=$3
task_image=${CMDB_POSTGRES_CLIENT_IMAGE:?pinned PostgreSQL client image required}
task_database=${CMDB_DATABASE:?explicit database required}
[[ $task_database == cmdb ]] || exit 1
task_contract=$task_release_dir/contracts/cmdb-postgresql-v1
task_receipt_dir=$(mktemp -d "$task_runtime_dir/verify.XXXXXXXX")
chmod 0700 "$task_receipt_dir"
client() {
  docker run --rm -i --network "$task_network" --env-file "$task_runtime_dir/bootstrap.env" -e "PGDATABASE=$task_database" -e "CMDB_TEST_ROLE=$1" "$task_image" sh -c '
    case "$CMDB_TEST_ROLE" in
      cmdb_migrator) PGPASSWORD=$CMDB_MIGRATOR_PASSWORD;;
      cmdb_writer) PGPASSWORD=$CMDB_WRITER_PASSWORD;;
      cmdb_reader) PGPASSWORD=$CMDB_READER_PASSWORD;;
      *) exit 64;;
    esac
    export PGPASSWORD
    exec psql -h cmdb-postgres -U "$CMDB_TEST_ROLE" -v ON_ERROR_STOP=1 -At
  '
}
client cmdb_migrator < "$task_contract/migrations/002_present_provider_summary.sql" >/dev/null
printf '%s\n' "UPDATE cmdb.resources SET lifecycle='retired' WHERE canonical_id='cmdb://gcp/open-platform-shared/compute/observability-shared-0/cmdb-uat-fixture' AND source='direct-gcloud-ssh-uat';" | client cmdb_writer >/dev/null
printf '%s\n' 'BEGIN; UPDATE cmdb.resources SET name=name WHERE false; ROLLBACK;' | client cmdb_reader > "$task_receipt_dir/reader-write.txt" 2>&1 && { echo 'reader unexpectedly permitted write' >&2; exit 1; }
grep -q 'permission denied for table resources' "$task_receipt_dir/reader-write.txt"
printf '%s\n' 'BEGIN; CREATE TABLE cmdb.__cmdb_ddl_permission_test (id integer); ROLLBACK;' | client cmdb_writer > "$task_receipt_dir/writer-ddl.txt" 2>&1 && { echo 'writer unexpectedly permitted DDL' >&2; exit 1; }
grep -q 'permission denied for schema cmdb' "$task_receipt_dir/writer-ddl.txt"
echo reader_write_denied=true
echo writer_ddl_denied=true
printf '%s\n' "SELECT provider||'|'||scope||'|'||resource_kind||'|'||provider_state||'|'||count(*) FROM cmdb.v_resource_current WHERE lifecycle='present' GROUP BY provider,scope,resource_kind,provider_state ORDER BY 1;" | client cmdb_reader
printf '%s\n' "SELECT collector||'|'||project_ref||'|'||outcome||'|'||resources_seen||'|'||coalesce(error_class,'') FROM cmdb.v_collection_health WHERE collector IN ('gcp-compute','gcp-cloudrun') ORDER BY started_at DESC LIMIT 6;" | client cmdb_reader
printf '%s\n' 'SELECT count(*) FROM cmdb.raw_snapshots;' | client cmdb_writer > "$task_receipt_dir/snapshot-count.txt"
printf 'raw_snapshots='; tail -n 1 "$task_receipt_dir/snapshot-count.txt"
echo "verification_receipt_dir=$(basename "$task_receipt_dir")"
