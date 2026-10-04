#!/usr/bin/env bash
set -euo pipefail

target="${1:-}"
cmdb_file="${CMDB_FILE:-cmdb/cmdb.json}"
environment="${REQUESTED_ENVIRONMENT:-}"
[[ "${environment}" =~ ^(uat|prod|sit)$ ]] || { echo "invalid environment" >&2; exit 2; }
. "$(dirname "${BASH_SOURCE[0]}")/cmdb-ssh-login.sh"
selfhost_cmdb_ssh_login "${cmdb_file}" "${target}" "${environment}"

ssh_opts=(-i "$HOME/.ssh/id_deploy" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
remote_command=("${sudo_prefix[@]}" bash -s)
ssh "${ssh_opts[@]}" "${ssh_user}@${host_ip}" "${remote_command[*]}" <<'REMOTE'
set -euo pipefail
container=web-saas-postgresql
if [ "$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)" != running ]; then
  echo "PostgreSQL container is not running; schema initialization stopped." >&2
  exit 1
fi
database_present="$(docker exec "$container" psql -U postgres -d postgres -XAtq -v ON_ERROR_STOP=1 \
  -c "SELECT 1 FROM pg_database WHERE datname='account'" 2>/dev/null || true)"
if [ "$database_present" != 1 ]; then
  echo "Account database is absent; empty-host initialization may proceed."
  exit 0
fi
table_count="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name <> 'spatial_ref_sys'")"
if ! [[ "$table_count" =~ ^[0-9]+$ ]]; then
  echo "Could not establish whether the Account database is empty; initialization stopped." >&2
  exit 1
fi
if [ "$table_count" -ne 0 ]; then
  echo "Account database contains ${table_count} public tables; refusing schema initialization on a nonempty host." >&2
  exit 1
fi
echo "Account database is empty; schema initialization may proceed."
REMOTE
