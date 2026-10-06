#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?private runner directory required}"
: "${NATIVE_INIT_SPEC_FILE:?fixed native schema contract required}"
: "${NATIVE_INIT_DRY_RUN:?explicit preview or apply required}"
: "${NATIVE_RECEIPT_FILE:?same-run receipt required}"
[[ "$NATIVE_INIT_DRY_RUN" == true || "$NATIVE_INIT_DRY_RUN" == false ]]
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-native-init-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]] || exit 2
[[ "$NATIVE_INIT_DRY_RUN" == true || "${NATIVE_DATA_GATE_VERIFIED:-}" == true ]] || exit 2
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/native_access_guard.sh"
python3 "$script_dir/native_init_host.py" --validate-only --spec "$NATIVE_INIT_SPEC_FILE" \
  --guard-directory "$script_dir" --dry-run "$NATIVE_INIT_DRY_RUN"
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" initialize-web-saas-native.yml
test -s "$NATIVE_RECEIPT_FILE"
jq -e --arg mode "$NATIVE_INIT_DRY_RUN" --slurpfile spec "$NATIVE_INIT_SPEC_FILE" '
  .environment == "prod" and .host == "web-saas-prod" and .database == "account" and
  .schema_sha256 == $spec[0].schema_sha256 and .migration_version == $spec[0].migration_version and
  .business_tables == $spec[0].business_tables and .business_rows == 0 and
  .accounts_commit == $spec[0].accounts_commit and .image_digest == $spec[0].image_digest and
  .writers_paused == true and .independent_disk_verified == true and
  .database_cutover_approved == false and
  (if $mode == "true" then .stage == "native_schema_preview" and .schema_initialized == false and
    (.result == "eligible" or .result == "eligible_absent_database") and .database_created == false
   else .stage == "native_schema_initialized" and .schema_initialized == true and .result == "initialized" end)
' "$NATIVE_RECEIPT_FILE" >/dev/null
