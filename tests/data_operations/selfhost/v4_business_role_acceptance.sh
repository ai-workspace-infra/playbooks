#!/usr/bin/env bash
set -euo pipefail

# Read-only contract check. Use a candidate database; this script never writes.
container="${V4_POSTGRES_CONTAINER:-web-saas-postgresql}"
role="${V4_BUSINESS_DB_ROLE:-account_user}"
[[ "$role" == "account_user" ]] || { echo 'business role must be account_user' >&2; exit 2; }

query="SELECT rolname, has_table_privilege(rolname,'public.users','INSERT'), has_table_privilege(rolname,'public.billing_ledger','INSERT'), has_table_privilege(rolname,'public.traffic_minute_buckets','INSERT') FROM pg_roles WHERE rolname='account_user';"
result="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -F '|' -c "$query")"
[[ "$result" == 'account_user|t|t|t' ]] || {
  echo 'FAIL: canonical account_user lacks expected business insert grants (role values are not emitted)' >&2
  exit 1
}
echo 'PASS: canonical account_user has expected business insert grants'
