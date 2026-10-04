#!/usr/bin/env bash
set -euo pipefail

source_backend="${ACCOUNTS_SOURCE_BACKEND:-supabase}"
backend="${ACCOUNTS_TARGET_BACKEND:-vps}"
mode="${ACCOUNTS_MIGRATION_MODE:-data}"
project_ref="${SUPABASE_PROJECT_REF:-}"
target_strategy="${SUPABASE_TARGET_EXISTING_STRATEGY:-reject}"

case "${source_backend}" in
  vps|supabase) ;;
  *)
    echo "::error::Unsupported Accounts source backend: ${source_backend}. Allowed: vps, supabase." >&2
    exit 1
    ;;
esac

case "${target_strategy}" in
  reject|replace_public|accounts_merge) ;;
  *)
    echo "::error::Unsupported Supabase target strategy: ${target_strategy}." >&2
    echo "Allowed strategies are reject, replace_public, or accounts_merge." >&2
    exit 1
    ;;
esac

case "${backend}:${mode}" in
  vps:data)
    echo "Migration target validated: VPS self-hosted PostgreSQL data flow (source=${source_backend})."
    ;;
  supabase:metadata|supabase:metadata_and_data)
    if [[ -n "${project_ref}" && ! "${project_ref}" =~ ^[a-z0-9]{20}$ ]]; then
      echo "::error::SUPABASE_PROJECT_REF must be a 20-character lowercase project ref." >&2
      exit 1
    fi
    if [[ "${target_strategy}" == "accounts_merge" && "${mode}" != "metadata_and_data" ]]; then
      echo "::error::SUPABASE_TARGET_EXISTING_STRATEGY=accounts_merge requires ACCOUNTS_MIGRATION_MODE=metadata_and_data." >&2
      exit 1
    fi
    if [[ "${source_backend}" == "supabase" ]]; then
      echo "Migration path validated: PROD Supabase -> UAT Supabase one-way data flow (strategy=${target_strategy})."
    else
      echo "Migration path validated: PROD VPS -> UAT Supabase one-way data flow (strategy=${target_strategy})."
    fi
    ;;
  *)
    echo "::error::Unsupported Accounts migration combination: backend=${backend}, mode=${mode}." >&2
    echo "Allowed combinations are backend=vps/mode=data or backend=supabase/mode=metadata|metadata_and_data." >&2
    exit 1
    ;;
esac
