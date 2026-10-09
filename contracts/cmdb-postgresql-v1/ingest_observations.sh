#!/usr/bin/env bash
# Owner: Playbooks. Caller: controlled artifact ingestion, never cloud discovery.
set -euo pipefail
if [[ $# != 4 ]]; then
  echo 'usage: ingest_observations.sh RUNTIME_DIR IAC_CONTRACT_DIR ENVELOPE_DIR DOCKER_NETWORK' >&2
  exit 64
fi
task_runtime_dir=$1
task_contract_dir=$2
task_envelope_dir=$3
task_network=$4
task_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
task_image=${CMDB_POSTGRES_CLIENT_IMAGE:?explicit pinned PostgreSQL client image required}
task_database=${CMDB_DATABASE:?explicit database required}
[[ $task_database == cmdb ]] || { echo 'database must be cmdb' >&2; exit 1; }
[[ -s $task_runtime_dir/bootstrap.env ]] || { echo 'runtime credentials are missing' >&2; exit 1; }
[[ $(stat -c '%a' "$task_runtime_dir/bootstrap.env") == 600 ]] || { echo 'runtime credential file must be mode 0600' >&2; exit 1; }
[[ $(stat -c '%U' "$task_runtime_dir/bootstrap.env") == root ]] || { echo 'runtime credential file must be root-owned' >&2; exit 1; }
docker network inspect "$task_network" >/dev/null
shopt -s nullglob
task_envelopes=("$task_envelope_dir"/*.json)
[[ ${#task_envelopes[@]} -gt 0 ]] || { echo 'no observation envelopes' >&2; exit 1; }
task_work_dir=$(mktemp -d "$task_runtime_dir/ingest.XXXXXXXX")
trap 'rm -f "$task_work_dir"/*.sql; rmdir "$task_work_dir"' EXIT
# Validate every artifact before the first database mutation.
for task_envelope in "${task_envelopes[@]}"; do
  python3 "$task_script_dir/render_ingest_sql.py" --contract-dir "$task_contract_dir" "$task_envelope" > "$task_work_dir/$(basename "$task_envelope" .json).sql"
done
install -d -m 0700 "$task_runtime_dir/backups"
task_backup=$task_runtime_dir/backups/cmdb-before-ingest-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump
umask 077
docker run --rm --network "$task_network" --env-file "$task_runtime_dir/bootstrap.env" -e "PGDATABASE=$task_database" "$task_image" sh -c 'PGPASSWORD="$CMDB_POSTGRES_PASSWORD" exec pg_dump -h cmdb-postgres -U postgres --format=custom' > "$task_backup"
[[ -s $task_backup ]] || { echo 'backup is empty' >&2; exit 1; }
docker run --rm -i "$task_image" pg_restore --list < "$task_backup" >/dev/null
for task_sql in "$task_work_dir"/*.sql; do
  docker run --rm -i --network "$task_network" --env-file "$task_runtime_dir/bootstrap.env" -e "PGDATABASE=$task_database" "$task_image" sh -c 'PGPASSWORD="$CMDB_WRITER_PASSWORD" exec psql -h cmdb-postgres -U cmdb_writer -v ON_ERROR_STOP=1' < "$task_sql" >/dev/null
  printf 'ingested=%s\n' "$(basename "$task_sql" .sql)"
done
printf 'backup=%s\n' "$(basename "$task_backup")"
