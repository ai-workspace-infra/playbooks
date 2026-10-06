#!/usr/bin/env bash
# Host owner only. Caller already verifies artifact/run provenance and opens access through IaC.
set -euo pipefail
: "${RUNNER_TEMP:?private runner directory required}"
: "${NATIVE_RECEIPT_FILE:?same-run sanitized receipt path required}"
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-native-standby-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]] || {
  echo 'Receipt must be a fresh same-run output.' >&2; exit 2;
}
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/native_access_guard.sh"
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" setup-web-saas-native-standby.yml
test -s "$NATIVE_RECEIPT_FILE"
jq -e --arg commit "$GITOPS_COMMIT" '.stage == "database_standby" and .host == "web-saas-prod" and
  .environment == "prod" and .writers_paused == true and
  .gitops_commit == $commit and .postgres_major == 17 and .independent_disk_verified == true and
  .schema_initialized == false and .database_cutover_approved == false' "$NATIVE_RECEIPT_FILE" >/dev/null
