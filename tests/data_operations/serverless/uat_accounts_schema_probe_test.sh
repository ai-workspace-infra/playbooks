#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
probe="${root}/scripts/data_operations/serverless/probe_accounts_schema.sh"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

cat >"${workdir}/psql" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == *'sslmode=require'* ]] || exit 3
case "$*" in
  *"to_regclass('public.schema_migrations')"*) printf 'absent\n' ;;
  *"to_regclass('public.account_lifecycle_events')"*) printf 'f\n' ;;
  *"subscription_valid_from"*) printf '4\n' ;;
  *"to_regclass('public.bridge_credentials')"*) printf 't\n' ;;
  *"to_regclass('public.overlay_registrations')"*) printf 't\n' ;;
  *"transport_kind"*) printf '4\n' ;;
  *"unnest(ARRAY"*) printf 'bridge_credentials_active_user_tenant_uk=true,bridge_credentials_user_tenant_idx=true,overlay_registrations_identity_pending_idx=true,overlay_registrations_network_created_idx=true,overlay_registrations_network_pending_idx=true,overlay_registrations_owner_created_idx=true\n' ;;
  *"wireguard_public_key_fingerprint"*) printf '7\n' ;;
  *"GREATEST(reltuples::bigint"*) printf '3\n' ;;
  *"information_schema.columns"*) printf '0\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "${workdir}/psql"

base=(
  "PATH=${workdir}:${PATH}"
  VAULT_ENV_PATH=uat
  PROJECT_REF=abcdefghijklmnopqrst
  TARGET_DSN=postgres://postgres.abcdefghijklmnopqrst:placeholder@aws-0-test.pooler.supabase.com:5432/postgres?sslmode=require
)
output="$(env "${base[@]}" bash "${probe}")"
[[ "${output}" == *'migration_version=absent, lifecycle_columns=0/6, lifecycle_events=f'* ]] || {
  echo 'Read-only UAT schema probe did not report the expected metadata.' >&2
  exit 1
}
[[ "${output}" == *'subscription_columns=4/4, bridge_credentials=t, overlay_registrations=t, overlay_transport_columns=4/4'* ]] || {
  echo 'Read-only UAT schema probe did not report prior migration shape.' >&2
  exit 1
}
[[ "${output}" == *'prior migration indexes: bridge_credentials_active_user_tenant_uk=true'* && "${output}" == *'overlay_registrations_owner_created_idx=true'* ]] || {
  echo 'Read-only UAT schema probe did not report prior migration index presence.' >&2
  exit 1
}
[[ "${output}" == *'overlay registration index prerequisites: columns=7/7, estimated_rows=3'* ]] || {
  echo 'Read-only UAT schema probe did not report overlay index prerequisites.' >&2
  exit 1
}
output="$(env "${base[@]}" TARGET_DSN=postgres://postgres.abcdefghijklmnopqrst:placeholder@aws-0-test.pooler.supabase.com:5432/postgres bash "${probe}")"
[[ "${output}" == *'migration_version=absent'* ]] || {
  echo 'Schema probe did not normalize an omitted TLS mode.' >&2
  exit 1
}
prod_output="$(env "${base[@]}" VAULT_ENV_PATH=prod bash "${probe}")"
[[ "${prod_output}" == *'prod Accounts schema: migration_version=absent'* ]] || {
  echo 'Read-only schema probe did not support the explicitly allowed PROD operation.' >&2
  exit 1
}
bad_target_output="$(env "${base[@]}" TARGET_DSN=postgres://postgres.abcdefghijklmnopqrst:placeholder@aws-0-test.pooler.supabase.com:6543/postgres?sslmode=require bash "${probe}" 2>&1)" && {
  echo 'Schema probe accepted a transaction-pooler target.' >&2
  exit 1
}
[[ "${bad_target_output}" == *'port_5432=False'* && "${bad_target_output}" != *'placeholder'* ]] || {
  echo 'Schema probe did not provide safe, redacted diagnostics.' >&2
  exit 1
}
if env "${base[@]}" TARGET_DSN=postgres://postgres.abcdefghijklmnopqrst:placeholder@aws-0-test.pooler.supabase.com:5432/postgres?sslmode=disable bash "${probe}" >/dev/null 2>&1; then
  echo 'Schema probe accepted disabled TLS.' >&2
  exit 1
fi
echo 'UAT Accounts schema read-only probe passed.'
