#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 2; }
[[ "${VAULT_ENV_PATH:-}" == "uat" ]] || fail "Accounts baseline adoption is UAT-only."
[[ "${PROJECT_REF:-}" =~ ^[a-z0-9]{20}$ ]] || fail "UAT Vault project identity is missing or invalid."
command -v psql >/dev/null || fail "psql is required."
command -v python3 >/dev/null || fail "Python 3 is required."

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! target_dsn="$(python3 "${script_dir}/normalize_accounts_uat_dsn.py")"; then
  fail "Target connection does not match the UAT Supabase session pooler project."
fi

psql "${target_dsn}" -X -q -v ON_ERROR_STOP=1 \
  -f "${script_dir}/uat_accounts_baseline_2026091401.sql" \
  >/dev/null || fail "UAT Accounts baseline transaction failed; no version was adopted."

verified="$(psql "${target_dsn}" -X -Atqc "SELECT version::text || ':' || dirty::text || ':' || (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name IN ('subscription_valid_from','subscription_valid_until','last_active_at','archived_at'))::text || ':' || (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname IN ('overlay_registrations_owner_created_idx','overlay_registrations_network_pending_idx','overlay_registrations_network_created_idx','overlay_registrations_identity_pending_idx'))::text FROM public.schema_migrations" 2>/dev/null)" || fail "Could not verify UAT Accounts baseline."
[[ "${verified}" == "2026091401:false:4:4" ]] || fail "UAT Accounts baseline verification mismatch."
echo "UAT Accounts expand-only baseline adopted: version 2026091401, subscription columns 4/4, overlay indexes 4/4."
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf 'UAT Accounts expand-only baseline adopted: `2026091401`, subscription columns `4/4`, overlay registration indexes `4/4`. Existing user values unchanged.\n' >>"${GITHUB_STEP_SUMMARY}"
fi
