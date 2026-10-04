#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
metadata="${repo_root}/scripts/data_operations/supabase_metadata_migration.sh"
merge="${repo_root}/scripts/data_operations/supabase_accounts_merge_migration.sh"
validator="${repo_root}/roles/uat_data_import/files/validate_config.py"

grep -Fq 'SOURCE_DB_USER="${SUPABASE_SOURCE_DB_USER:-readonly}"' "${metadata}"
grep -Fq 'SOURCE_SSH_KEY_PATH="${SUPABASE_SOURCE_SSH_KEY_PATH:-${HOME}/.ssh/id_deploy}"' "${metadata}"
grep -Fq '  -i "${SOURCE_SSH_KEY_PATH}"' "${metadata}"
grep -Fq 'docker exec "${SOURCE_CONTAINER}" pg_dump' "${metadata}"
grep -Fq 'source DB role must remain readonly' "${merge}"
grep -Fq 'SUPABASE_SOURCE_DSN' "${merge}"
grep -Fq 'Accounts merge requires SUPABASE_MIGRATION_MODE=metadata_and_data' "${merge}"
grep -Fq 'SUPABASE_TARGET_EXISTING_STRATEGY' "${metadata}"
grep -Fq 'Accounts merge target must be a Supabase DSN' "${merge}"
grep -Fq 'VAULT_ENV_PATH:-}" == "uat"' "${merge}"
grep -Fq 'supabase_target_existing_strategy=replace_public is prohibited' "${validator}"

if rg -n 'MIGRATION_SOURCE_DSN|SOURCE_SSH_TARGET_PORT|SOURCE_TUNNEL_LOCAL_PORT' "${metadata}"; then
  echo "Metadata migration must not use a runner-side source DSN or tunnel transport." >&2
  exit 1
fi

echo "supabase_migration_implementation_contract_test: PASS"
