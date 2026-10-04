#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
accounts_root="${ACCOUNTS_REPO_ROOT:-}"
expected_accounts_sha="9ebd21c134f9b98f47c848f99a154f503c6b59ce"
database_url="${TEST_DATABASE_URL:-}"
sentinel="${root}/scripts/data_operations/serverless/accounts_uat_data_sentinel.sh"
migration="${accounts_root}/sql/migrations/2026092301_account_lifecycle_states.up.sql"
schema="${accounts_root}/sql/schema.sql"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

[[ -n "${database_url}" && -n "${accounts_root}" ]] || { echo 'TEST_DATABASE_URL and ACCOUNTS_REPO_ROOT are required.' >&2; exit 2; }
if git -C "${accounts_root}" rev-parse HEAD >/dev/null 2>&1; then
  [[ "$(git -C "${accounts_root}" rev-parse HEAD)" == "${expected_accounts_sha}" ]] || {
    echo 'Integration test requires the immutable Accounts PR #170 merge commit.' >&2
    exit 2
  }
else
  [[ "${ACCOUNTS_SOURCE_REVISION:-}" == "${expected_accounts_sha}" ]] || {
    echo 'An archived Accounts source tree must declare the immutable PR #170 merge revision.' >&2
    exit 2
  }
fi
[[ -f "${schema}" && -f "${migration}" ]] || { echo 'Pinned Accounts baseline schema or PR #170 migration is missing.' >&2; exit 2; }
command -v psql >/dev/null || { echo 'psql is required for the PostgreSQL 17 integration test.' >&2; exit 2; }
server_major="$(psql "${database_url}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT current_setting('server_version_num')::int / 10000" 2>/dev/null)" || {
  echo 'Could not verify the integration database version.' >&2
  exit 1
}
[[ "${server_major}" == 17 ]] || { echo 'Integration test must run against PostgreSQL 17.' >&2; exit 1; }

# Extract only the two non-destructive baseline table definitions. Never execute
# the full Accounts schema.sql because it contains reset/drop statements.
python3 - "${schema}" "${tmp}/baseline-tables.sql" <<'PY'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
tables = []
for name in ("users", "subscriptions"):
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS public\.{name}\s*\([\s\S]*?\n\);",
        source,
    )
    if not match:
        raise SystemExit(f"Actual Accounts baseline table definition missing: {name}")
    ddl = match.group(0)
    if not re.search(r"\buuid\s+UUID\s+PRIMARY KEY\b", ddl, re.IGNORECASE):
        raise SystemExit(f"Actual Accounts baseline {name} table does not use uuid UUID PRIMARY KEY")
    tables.append(ddl)
Path(sys.argv[2]).write_text("\n\n".join(tables) + "\n")
PY

psql "${database_url}" -X -v ON_ERROR_STOP=1 -f "${tmp}/baseline-tables.sql" >/dev/null 2>&1 || {
  echo 'Could not create actual Accounts baseline users/subscriptions tables in the disposable PostgreSQL database.' >&2
  exit 1
}
psql "${database_url}" -X -v ON_ERROR_STOP=1 -Atqc "
  INSERT INTO public.users (uuid, username, password, email, groups, permissions, proxy_uuid, subscription_valid_from)
  VALUES
    ('00000000-0000-4000-8000-000000000001', 'sentinel-user-a', 'fixture-only', 'a@example.invalid', '[\"team-a\"]', '[\"read\"]', '10000000-0000-4000-8000-000000000001', '2026-09-01T00:00:00Z'),
    ('00000000-0000-4000-8000-000000000002', 'sentinel-user-b', 'fixture-only', 'b@example.invalid', '[\"team-b\"]', '[\"write\"]', '10000000-0000-4000-8000-000000000002', '2026-09-02T00:00:00Z');
  INSERT INTO public.subscriptions (uuid, user_uuid, provider, payment_method, kind, external_id, status, meta)
  VALUES ('20000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'fixture-provider', 'fixture-payment', 'subscription', 'fixture-external-id', 'active', '{\"plan\":\"fixture\"}');
" >/dev/null 2>&1 || {
  echo 'Could not seed disposable Accounts baseline rows for sentinel integration.' >&2
  exit 1
}

capture_sentinel() {
  TARGET_DSN="${database_url}" bash "${sentinel}" 2>/dev/null
}
sentinel_before="$(capture_sentinel)" || { echo 'Initial private sentinel calculation failed.' >&2; exit 1; }

psql "${database_url}" -X -v ON_ERROR_STOP=1 -f "${migration}" >/dev/null 2>&1 || {
  echo 'Actual Accounts PR #170 migration failed against baseline-compatible PostgreSQL 17 tables.' >&2
  exit 1
}
sentinel_after="$(capture_sentinel)" || { echo 'Post-migration private sentinel calculation failed.' >&2; exit 1; }
[[ "${sentinel_after}" == "${sentinel_before}" ]] || {
  echo 'Full-row users/subscriptions sentinel changed across the actual PR #170 migration.' >&2
  exit 1
}

expect_change_detected() {
  local description="$1"
  local sql="$2"
  local baseline="$3"
  psql "${database_url}" -X -v ON_ERROR_STOP=1 -Atqc "${sql}" >/dev/null 2>&1 || {
    echo "Could not apply disposable ${description} sentinel mutation." >&2
    exit 1
  }
  local changed
  changed="$(capture_sentinel)" || { echo "Sentinel calculation failed after ${description} mutation." >&2; exit 1; }
  [[ "${changed}" != "${baseline}" ]] || {
    echo "Full-row sentinel failed to detect ${description} mutation." >&2
    exit 1
  }
}

expect_change_detected 'user groups' \
  "UPDATE public.users SET groups='[\"team-changed\"]'::jsonb WHERE uuid='00000000-0000-4000-8000-000000000001'" \
  "${sentinel_after}"
groups_changed="$(capture_sentinel)"
expect_change_detected 'user UUID' \
  "UPDATE public.users SET uuid='00000000-0000-4000-8000-000000000003' WHERE uuid='00000000-0000-4000-8000-000000000002'" \
  "${groups_changed}"
uuid_changed="$(capture_sentinel)"
expect_change_detected 'subscription row' \
  "UPDATE public.subscriptions SET meta='{\"plan\":\"changed\"}'::jsonb WHERE uuid='20000000-0000-4000-8000-000000000001'" \
  "${uuid_changed}"

[[ "${sentinel_before}" == $'users:2:'* && "${sentinel_before}" == *$'\nsubscriptions:1:'* ]] || {
  echo 'Sentinel count projection did not cover both actual UUID-keyed tables.' >&2
  exit 1
}
echo 'PostgreSQL 17 full-row sentinel integration passed (users=2, subscriptions=1); group, user UUID and subscription mutations were detected.'
