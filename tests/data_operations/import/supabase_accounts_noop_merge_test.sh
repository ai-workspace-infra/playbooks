#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/bin"
cat >"${tmp}/bin/migratectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  export)
    while (( $# )); do
      if [[ "$1" == --output ]]; then shift; printf 'snapshot\n' >"$1"; exit 0; fi
      shift
    done
    exit 1 ;;
  import)
    printf 'Import preview: users inserted=0 updated=0 skipped=23\n'
    printf 'Identities inserted=0 updated=0 deleted=0\n'
    printf 'Sessions inserted=0 updated=0 deleted=0\n'
    exit 0 ;;
esac
exit 1
EOF
cat >"${tmp}/bin/psql" <<'EOF'
#!/usr/bin/env bash
exit 77
EOF
cat >"${tmp}/bin/pg_dump" <<'EOF'
#!/usr/bin/env bash
exit 77
EOF
chmod +x "${tmp}/bin/"*

output="$(env \
  PATH="${tmp}/bin:${PATH}" \
  VAULT_ENV_PATH=uat \
  SUPABASE_SOURCE_BACKEND=supabase \
  SUPABASE_SOURCE_DSN='postgres://readonly:placeholder@aws-0-prod.pooler.supabase.com:5432/postgres?sslmode=require' \
  SUPABASE_TARGET_DSN='postgres://postgres.abcdefghijklmnopqrst:placeholder@aws-0-test.pooler.supabase.com:5432/postgres?sslmode=require' \
  SUPABASE_VAULT_PROJECT_REF=abcdefghijklmnopqrst \
  SUPABASE_MIGRATION_MODE=metadata_and_data \
  SUPABASE_METADATA_DRY_RUN=false \
  SUPABASE_TARGET_CONNECTION_MODE=session_pooler \
  MIGRATECTL_BIN="${tmp}/bin/migratectl" \
  SUPABASE_ACCOUNTS_SNAPSHOT_FILE="${tmp}/snapshot.yaml" \
  SUPABASE_TARGET_BACKUP_FILE="${tmp}/backup.sql" \
  bash "${root}/scripts/data_operations/supabase_accounts_merge_migration.sh")"

[[ "${output}" == *'completed as a verified no-op'* ]] || { echo 'No-op was not reported.' >&2; exit 1; }
[[ ! -e "${tmp}/snapshot.yaml" && ! -e "${tmp}/backup.sql" ]] || { echo 'Sensitive snapshot or backup remained.' >&2; exit 1; }
echo 'UAT Accounts no-op merge guard passed.'
