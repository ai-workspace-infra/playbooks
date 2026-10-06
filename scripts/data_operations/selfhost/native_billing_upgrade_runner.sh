#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?private runner directory required}"
: "${NATIVE_BILLING_SPEC_FILE:?fixed combined schema contract required}"
: "${BILLING_CHECKOUT:?fixed Billing checkout required}"
: "${BILLING_COMMIT:?fixed Billing commit required}"
: "${NATIVE_BILLING_DRY_RUN:?explicit preview or apply required}"
: "${NATIVE_RECEIPT_FILE:?same-run receipt required}"
[[ "$NATIVE_BILLING_DRY_RUN" == true || "$NATIVE_BILLING_DRY_RUN" == false ]]
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-native-billing-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]] || exit 2
[[ "$NATIVE_BILLING_DRY_RUN" == true || "${NATIVE_DATA_GATE_VERIFIED:-}" == true ]] || exit 2
[[ "$BILLING_COMMIT" =~ ^[0-9a-f]{40}$ && "$(git -C "$BILLING_CHECKOUT" rev-parse HEAD)" == "$BILLING_COMMIT" ]] || exit 2
[[ -z "$(git -C "$BILLING_CHECKOUT" status --porcelain --untracked-files=all)" ]] || exit 2
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$NATIVE_BILLING_SPEC_FILE" "$BILLING_CHECKOUT" "$BILLING_COMMIT" <<'PY'
import json, pathlib, sys
spec=json.loads(pathlib.Path(sys.argv[1]).read_text())['billing']
manifest=json.loads((pathlib.Path(sys.argv[2])/'sql/native-finops.manifest.json').read_text())
assert spec['commit']==sys.argv[3] and manifest['format']==1
assert {k:v for k,v in spec.items() if k!='commit'}==manifest
PY
python3 "$script_dir/native_billing_upgrade_host.py" --validate-only --spec "$NATIVE_BILLING_SPEC_FILE" \
  --migration "$BILLING_CHECKOUT/sql/migrations/2026100701_cloud_vendor_costs.up.sql" \
  --guard-directory "$script_dir" --dry-run "$NATIVE_BILLING_DRY_RUN"
source "$script_dir/native_access_guard.sh"
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" upgrade-web-saas-native-billing.yml
test -s "$NATIVE_RECEIPT_FILE"
jq -e --arg mode "$NATIVE_BILLING_DRY_RUN" --slurpfile spec "$NATIVE_BILLING_SPEC_FILE" '
  .environment == "prod" and .host == "web-saas-prod" and .database == "account" and
  .billing_commit == $spec[0].billing.commit and .migration_sha256 == $spec[0].billing.migration_sha256 and
  .accounts_commit == $spec[0].initialization.accounts_commit and .image_digest == $spec[0].initialization.image_digest and
  .target_version == 2026100701 and .business_rows == 0 and
  .writers_paused == true and .independent_disk_verified == true and .database_cutover_approved == false and
  (if $mode == "true" then .stage == "native_billing_schema_preview" and .result == "eligible" and .schema_changed == false and
    (.migration_version == 2026100601 or .migration_version == 2026100701)
   else .stage == "native_billing_schema_upgraded" and .result == "upgraded" and .migration_version == 2026100701 end) and
  .business_tables == ($spec[0].initialization.business_tables +
    (if .migration_version == 2026100701 then $spec[0].billing.business_tables else [] end) | sort)
' "$NATIVE_RECEIPT_FILE" >/dev/null
