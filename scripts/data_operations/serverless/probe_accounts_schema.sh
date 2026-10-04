#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 2; }
environment="${VAULT_ENV_PATH:-}"
[[ "${environment}" == "uat" || "${environment}" == "prod" ]] || fail "Accounts schema probe supports only UAT or PROD."
[[ "${PROJECT_REF:-}" =~ ^[a-z0-9]{20}$ ]] || fail "Vault PROJECT_REF is missing or invalid."
command -v psql >/dev/null || fail "psql is required."
command -v python3 >/dev/null || fail "Python 3 is required."

if ! dsn="$(python3 "$(dirname "${BASH_SOURCE[0]}")/normalize_accounts_uat_dsn.py")"; then
  fail "Target connection does not match the selected Supabase session pooler project."
fi
table="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT COALESCE(to_regclass('public.schema_migrations')::text, 'absent')" 2>/dev/null)" || fail "UAT database schema probe failed."
version="absent"
if [[ "${table}" != "absent" ]]; then
  version="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT version::text || ':' || dirty::text FROM public.schema_migrations LIMIT 1" 2>/dev/null)" || fail "Could not inspect schema_migrations."
  [[ -n "${version}" ]] || version="empty"
fi
columns="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name IN ('account_lifecycle_state','account_lifecycle_changed_at','account_lifecycle_actor_type','account_lifecycle_actor_ref','account_lifecycle_reason','account_lifecycle_transition_id')" 2>/dev/null)" || fail "Could not inspect lifecycle columns."
events="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('public.account_lifecycle_events') IS NOT NULL" 2>/dev/null)" || fail "Could not inspect lifecycle event table."
subscription_columns="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name IN ('subscription_valid_from','subscription_valid_until','last_active_at','archived_at')" 2>/dev/null)" || fail "Could not inspect subscription baseline."
bridge_credentials="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('public.bridge_credentials') IS NOT NULL" 2>/dev/null)" || fail "Could not inspect bridge baseline."
overlay_registrations="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('public.overlay_registrations') IS NOT NULL" 2>/dev/null)" || fail "Could not inspect overlay registration baseline."
overlay_transport_columns="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='overlay_networks' AND column_name IN ('transport_kind','transport_path','transport_mode','transport_host')" 2>/dev/null)" || fail "Could not inspect overlay transport baseline."
prior_indexes="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT string_agg(expected.name || '=' || (to_regclass('public.' || expected.name) IS NOT NULL)::text, ',' ORDER BY expected.name) FROM unnest(ARRAY['bridge_credentials_user_tenant_idx','bridge_credentials_active_user_tenant_uk','overlay_registrations_owner_created_idx','overlay_registrations_network_pending_idx','overlay_registrations_network_created_idx','overlay_registrations_identity_pending_idx']) AS expected(name)" 2>/dev/null)" || fail "Could not inspect prior migration indexes."
overlay_index_columns="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='overlay_registrations' AND column_name = ANY(ARRAY['owner_user_id','created_at','network_id','status','expires_at','device_id','wireguard_public_key_fingerprint'])" 2>/dev/null)" || fail "Could not inspect overlay registration index columns."
overlay_rows_estimate="$(psql "${dsn}" -X -v ON_ERROR_STOP=1 -Atqc "SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE oid = 'public.overlay_registrations'::regclass" 2>/dev/null)" || fail "Could not inspect overlay registration table size."
echo "${environment} Accounts schema: migration_version=${version}, lifecycle_columns=${columns}/6, lifecycle_events=${events}."
echo "${environment} Accounts prior migration shape: subscription_columns=${subscription_columns}/4, bridge_credentials=${bridge_credentials}, overlay_registrations=${overlay_registrations}, overlay_transport_columns=${overlay_transport_columns}/4."
echo "${environment} Accounts prior migration indexes: ${prior_indexes}."
echo "${environment} overlay registration index prerequisites: columns=${overlay_index_columns}/7, estimated_rows=${overlay_rows_estimate}."
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf 'Accounts %s schema probe (read-only): version `%s`, lifecycle columns `%s/6`, event table `%s`.\n' "${environment}" "${version}" "${columns}" "${events}" >>"${GITHUB_STEP_SUMMARY}"
  printf 'Accounts %s prior migration shape: subscription columns `%s/4`, bridge table `%s`, overlay registration table `%s`, overlay transport columns `%s/4`.\n' "${environment}" "${subscription_columns}" "${bridge_credentials}" "${overlay_registrations}" "${overlay_transport_columns}" >>"${GITHUB_STEP_SUMMARY}"
  printf 'Accounts %s prior migration index presence: `%s`.\n' "${environment}" "${prior_indexes}" >>"${GITHUB_STEP_SUMMARY}"
  printf 'Overlay registration index prerequisites: columns `%s/7`, estimated rows `%s`.\n' "${overlay_index_columns}" "${overlay_rows_estimate}" >>"${GITHUB_STEP_SUMMARY}"
fi
