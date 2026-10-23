#!/usr/bin/env bash
set -euo pipefail

# Read-only contract check. Use a candidate database; this script never writes.
generation="${GENERATION_TARGET_GENERATION:-}"
container="${GENERATION_POSTGRES_CONTAINER:-}"
role="${GENERATION_BUSINESS_DB_ROLE:-account_user}"
[[ "$generation" =~ ^prod-v[1-9][0-9]*$ && "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
  echo 'target generation and exact candidate PostgreSQL container are required' >&2
  exit 2
}
[[ "$(hostname -s)" == "web-saas-${generation}" ]] || {
  echo 'execution host does not match the requested candidate generation' >&2
  exit 2
}
[[ "$role" == "account_user" ]] || { echo 'business role must be account_user' >&2; exit 2; }

query="SELECT rolname, has_table_privilege(rolname,'public.users','INSERT'), has_table_privilege(rolname,'public.billing_ledger','INSERT'), has_table_privilege(rolname,'public.traffic_minute_buckets','INSERT') FROM pg_roles WHERE rolname='account_user';"
result="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -F '|' -c "$query")"
[[ "$result" == 'account_user|t|t|t' ]] || {
  echo 'FAIL: canonical account_user lacks expected business insert grants (role values are not emitted)' >&2
  exit 1
}
echo "PASS: ${generation} candidate account_user has expected business insert grants"
