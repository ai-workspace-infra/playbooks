#!/usr/bin/env bash
set -euo pipefail

# Read-only live acceptance. Run on an isolated candidate with an already
# active, normally used client; this script never creates traffic or ledger rows.
container="${V4_POSTGRES_CONTAINER:-web-saas-postgresql}"
node_id="${V4_TRAFFIC_NODE_ID:-}"
[[ -n "$node_id" ]] || { echo 'V4_TRAFFIC_NODE_ID is required' >&2; exit 2; }

query="SELECT count(DISTINCT bucket_start), coalesce(sum(total_bytes),0) FROM traffic_minute_buckets WHERE node_id = :'node_id' AND bucket_start >= now() - interval '5 minutes' AND total_bytes > 0;"
result="$(docker exec "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -v "node_id=$node_id" -c "$query")"
read -r bucket_count byte_sum <<<"${result//|/ }"
[[ "$bucket_count" =~ ^[0-9]+$ && "$byte_sum" =~ ^[0-9]+$ ]] || { echo 'invalid read-only traffic query result' >&2; exit 1; }
if (( bucket_count < 2 || byte_sum == 0 )); then
  echo "FAIL: expected genuine positive-byte buckets in at least two recent minute windows (buckets=${bucket_count}, bytes=${byte_sum})" >&2
  exit 1
fi
echo "PASS: recent positive-byte buckets=${bucket_count}; bytes=${byte_sum}"
