#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
script="${repo_root}/scripts/data_operations/supabase_metadata_migration.sh"
test_dir="$(mktemp -d)"
trap 'rm -rf "${test_dir}"' EXIT

if SUPABASE_TARGET_DSN='postgres://postgres.project:password@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require' \
  SUPABASE_VAULT_PROJECT_REF='project' \
  SUPABASE_METADATA_DRY_RUN=true \
  bash "${script}" >"${test_dir}/output" 2>&1; then
  echo "Expected a Supabase migration without a PROD source SSH host to be rejected" >&2
  exit 1
fi

grep -Fq 'SUPABASE_SOURCE_TUNNEL_HOST is not configured' "${test_dir}/output"
grep -Fq 'Configure the PROD PostgreSQL SSH host' "${test_dir}/output"

echo "supabase_metadata_migration_source_guard_test: PASS"
