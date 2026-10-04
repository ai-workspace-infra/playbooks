#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
validator="${repo_root}/scripts/data_operations/validate_accounts_migration_target.sh"

ACCOUNTS_TARGET_BACKEND=vps ACCOUNTS_MIGRATION_MODE=data bash "${validator}" >/dev/null
SUPABASE_PROJECT_REF=iqkxspmhcfqmhkbjdoms ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata bash "${validator}" >/dev/null
SUPABASE_PROJECT_REF=iqkxspmhcfqmhkbjdoms ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null
ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null
ACCOUNTS_SOURCE_BACKEND=supabase ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null
ACCOUNTS_SOURCE_BACKEND=vps ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null

if ACCOUNTS_TARGET_BACKEND=vps ACCOUNTS_MIGRATION_MODE=metadata bash "${validator}" >/dev/null 2>&1; then
  echo "VPS target must reject Supabase metadata modes" >&2
  exit 1
fi
if SUPABASE_PROJECT_REF=invalid ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null 2>&1; then
  echo "Supabase target must reject an invalid explicit project ref" >&2
  exit 1
fi
if ACCOUNTS_SOURCE_BACKEND=invalid ACCOUNTS_TARGET_BACKEND=supabase ACCOUNTS_MIGRATION_MODE=metadata_and_data bash "${validator}" >/dev/null 2>&1; then
  echo "Supabase migration must reject an invalid source backend" >&2
  exit 1
fi

echo "data_migration_mode_contract_test: PASS"
