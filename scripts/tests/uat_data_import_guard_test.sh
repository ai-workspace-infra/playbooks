#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATOR="${ROOT}/roles/uat_data_import/files/validate_config.py"
export CORRELATION_ID=uat-data-import-guard-test

expect_reject() {
  local environment="$1" config="$2" label="$3"
  if REQUESTED_ENVIRONMENT="${environment}" CONFIG_JSON="${config}" python3 "${VALIDATOR}" >/dev/null 2>&1; then
    echo "FAIL: expected rejection: ${label}" >&2
    exit 1
  fi
}

expect_accept() {
  local environment="$1" config="$2" label="$3"
  if ! REQUESTED_ENVIRONMENT="${environment}" CONFIG_JSON="${config}" python3 "${VALIDATOR}" >/dev/null; then
    echo "FAIL: expected acceptance: ${label}" >&2
    exit 1
  fi
}

expect_reject uat '{}' 'missing explicit confirmation'
expect_reject uat '{broken json' 'malformed JSON'
expect_reject uat '{"confirm_legacy_import":false}' 'false confirmation'
expect_reject prod '{"confirm_legacy_import":true}' 'PROD environment'
expect_reject uat '{"confirm_legacy_import":true,"vault_env_path":"prod"}' 'PROD Vault environment'
expect_reject uat '{"confirm_legacy_import":true,"supabase_target_existing_strategy":"replace_public"}' 'replace_public'
expect_reject uat '{"confirm_legacy_import":true,"supabase_target_confirm_replace":true}' 'replace confirmation'
expect_reject uat '{"confirm_legacy_import":true,"accounts_target_backend":"vps","accounts_migration_mode":"metadata"}' 'metadata mode with VPS target'
expect_reject uat '{"confirm_legacy_import":true,"accounts_target_backend":"supabase","accounts_migration_mode":"data"}' 'data mode with Supabase target'
expect_reject uat '{"confirm_legacy_import":true,"accounts_target_backend":"supabase","accounts_migration_mode":"metadata","supabase_target_existing_strategy":"accounts_merge"}' 'invalid merge mode mapping'
expect_reject uat '{"confirm_legacy_import":true,"accounts_migration_mode":true}' 'wrong field type'
expect_reject uat '{"confirm_legacy_import":true,"supabase_vault_path":"kv/data/prod/serverless/supabase"}' 'PROD Vault path'
expect_reject uat '{"confirm_legacy_import":true,"migration_scope":"site"}' 'unsupported site scope'
expect_accept uat '{"confirm_legacy_import":true}' 'default SSH to VPS dry run'
expect_accept uat '{"confirm_legacy_import":true,"accounts_target_backend":"supabase","accounts_migration_mode":"metadata_and_data","supabase_target_existing_strategy":"accounts_merge"}' 'Supabase Accounts merge'

echo 'UAT data import guard tests passed; no database connection was opened.'
