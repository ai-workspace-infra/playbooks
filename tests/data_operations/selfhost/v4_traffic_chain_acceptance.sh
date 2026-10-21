#!/usr/bin/env bash
set -euo pipefail

# Read-only live acceptance. Run on an isolated candidate with an already
# active, normally used client; this script never creates traffic or ledger rows.
container="${V4_POSTGRES_CONTAINER:-web-saas-postgresql}"
node_id="${V4_TRAFFIC_NODE_ID:-}"
allow_zero_traffic="${V4_ALLOW_ZERO_TRAFFIC:-0}"
receipt_json="${V4_RECEIPT_JSON:-}"
case_id="${V4_CASE_ID:-v4_traffic_chain_acceptance}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-zero-traffic)
      allow_zero_traffic="1"
      shift
      ;;
    --receipt-json)
      [[ $# -ge 2 ]] || { echo '--receipt-json requires a path argument' >&2; exit 2; }
      receipt_json="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

[[ -n "$node_id" ]] || { echo 'V4_TRAFFIC_NODE_ID is required' >&2; exit 2; }

if [[ "$allow_zero_traffic" == "1" || "$allow_zero_traffic" == "true" ]]; then
  result="$(docker exec -i "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -v "node_id=$node_id" <<'SQL'
WITH recent_buckets AS (
  SELECT count(DISTINCT bucket_start) AS b_cnt, coalesce(sum(total_bytes), 0) AS b_sum
  FROM traffic_minute_buckets
  WHERE node_id = :'node_id'
    AND bucket_start >= now() - interval '5 minutes'
),
recent_checkpoints AS (
  SELECT count(*) AS c_cnt
  FROM traffic_stat_checkpoints
  WHERE node_id = :'node_id'
    AND last_seen_at >= now() - interval '5 minutes'
)
SELECT GREATEST(recent_buckets.b_cnt, recent_checkpoints.c_cnt), recent_buckets.b_sum
FROM recent_buckets, recent_checkpoints;
SQL
)"
else
  result="$(docker exec -i "$container" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -v "node_id=$node_id" <<'SQL'
SELECT count(DISTINCT bucket_start), coalesce(sum(total_bytes),0)
FROM traffic_minute_buckets
WHERE node_id = :'node_id'
  AND bucket_start >= now() - interval '5 minutes'
  AND total_bytes > 0;
SQL
)"
fi

read -r bucket_count byte_sum <<<"${result//|/ }"
[[ "$bucket_count" =~ ^[0-9]+$ && "$byte_sum" =~ ^[0-9]+$ ]] || { echo 'invalid read-only traffic query result' >&2; exit 1; }

write_receipt() {
  local status="$1"
  local traffic_state="$2"
  if [[ -z "$receipt_json" ]]; then
    return 0
  fi
  python3 - "$receipt_json" "$case_id" "$node_id" "$status" "$traffic_state" "$bucket_count" "$byte_sum" <<'PY'
import datetime
import hashlib
import json
from pathlib import Path
import sys

path, case_id, node_id, status, traffic_state, bucket_count, byte_sum = sys.argv[1:8]
node_hash = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
payload = {
    "case_id": case_id,
    "node_id_hash": node_hash,
    "status": status,
    "traffic_state": traffic_state,
    "bucket_count": int(bucket_count),
    "byte_sum": int(byte_sum),
    "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

if (( bucket_count >= 2 && byte_sum > 0 )); then
  write_receipt "PASS" "positive_traffic"
  echo "PASS: recent positive-byte buckets=${bucket_count}; bytes=${byte_sum}"
  exit 0
fi

if [[ "$allow_zero_traffic" == "1" || "$allow_zero_traffic" == "true" ]] && (( bucket_count >= 2 && byte_sum == 0 )); then
  write_receipt "PASS" "zero_traffic"
  echo "PASS (ZERO_TRAFFIC): recent buckets=${bucket_count}; bytes=0"
  exit 0
fi

write_receipt "FAIL" "insufficient_buckets"
echo "FAIL: expected genuine positive-byte buckets in at least two recent minute windows (buckets=${bucket_count}, bytes=${byte_sum})" >&2
exit 1
