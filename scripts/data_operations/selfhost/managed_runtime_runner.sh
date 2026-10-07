#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?private runner directory required}"
: "${MANAGED_RUNTIME_SPEC_FILE:?fixed image contract required}"
: "${MANAGED_RUNTIME_DRY_RUN:?explicit preview or qualification required}"
: "${NATIVE_RECEIPT_FILE:?same-run receipt required}"
[[ "$MANAGED_RUNTIME_DRY_RUN" == true || "$MANAGED_RUNTIME_DRY_RUN" == false ]]
[[ "$MANAGED_RUNTIME_DRY_RUN" == true || "${MANAGED_RUNTIME_GATE_VERIFIED:-}" == true ]]
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-managed-runtime-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]]
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/native_access_guard.sh"
python3 "$script_dir/managed_runtime_host.py" --validate-only --spec "$MANAGED_RUNTIME_SPEC_FILE" --dry-run "$MANAGED_RUNTIME_DRY_RUN"
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-managed-runtime-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" qualify-web-saas-managed-runtime.yml
jq -e --arg mode "$MANAGED_RUNTIME_DRY_RUN" --slurpfile spec "$MANAGED_RUNTIME_SPEC_FILE" '
  .schema == 1 and .environment == "prod" and .host == "web-saas-prod" and
  .services == $spec[0].services and .runtime_role == "standby" and
  .database_connected == false and .schema_verified == false and .application_deployed == false and
  .business_requests_enabled == false and .background_writers == false and .database_cutover_approved == false and
  (if $mode == "true" then .stage == "managed_image_preview" else .stage == "managed_images_qualified" end)
' "$NATIVE_RECEIPT_FILE" >/dev/null
