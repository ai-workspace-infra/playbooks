#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 2; }

target_dsn="${TARGET_DSN:-}"
[[ -n "${target_dsn}" ]] || fail "UAT target connection is required for the private data sentinel."
command -v psql >/dev/null || fail "psql is required to calculate the private data sentinel."
command -v python3 >/dev/null || fail "Python 3 is required to calculate the private data sentinel."

# Complete row JSON flows directly from psql into a hashing process; it is never
# written to a file, standard output, workflow log, step summary, or artifact.
sentinel_sql="
SELECT 'users', count(*)::text, COALESCE(jsonb_agg(to_jsonb(u) - ARRAY[
  'account_lifecycle_state',
  'account_lifecycle_changed_at',
  'account_lifecycle_actor_type',
  'account_lifecycle_actor_ref',
  'account_lifecycle_reason',
  'account_lifecycle_transition_id'
]::text[] ORDER BY u.uuid), '[]'::jsonb)::text
FROM public.users AS u
UNION ALL
SELECT 'subscriptions', count(*)::text, COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.uuid), '[]'::jsonb)::text
FROM public.subscriptions AS s
"
result="$(psql "${target_dsn}" -X -v ON_ERROR_STOP=1 -A -t -F $'\t' -c "${sentinel_sql}" 2>/dev/null | python3 -c '
import hashlib
import json
import re
import sys

results = {}
for line in sys.stdin:
    fields = line.rstrip("\n").split("\t", 2)
    if len(fields) != 3:
        raise SystemExit(2)
    table, count_text, projection = fields
    if table not in ("users", "subscriptions") or table in results or not re.fullmatch(r"[0-9]+", count_text):
        raise SystemExit(2)
    rows = json.loads(projection)
    count = int(count_text)
    if not isinstance(rows, list) or len(rows) != count:
        raise SystemExit(2)
    results[table] = (count, hashlib.sha256(projection.encode("utf-8")).hexdigest())

if set(results) != {"users", "subscriptions"}:
    raise SystemExit(2)
for table in ("users", "subscriptions"):
    count, digest = results[table]
    print(f"{table}:{count}:{digest}")
' 2>/dev/null)" || fail "Could not calculate private UAT users/subscriptions sentinel."
printf '%s' "${result}"
