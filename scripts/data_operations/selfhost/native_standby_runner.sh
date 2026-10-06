#!/usr/bin/env bash
# Host owner only. Caller already verifies artifact/run provenance and opens access through IaC.
set -euo pipefail
: "${CMDB_DIR:?resource artifact directory required}"
: "${EXPECTED_CMDB_SHA256:?reviewed CMDB checksum required}"
: "${NATIVE_ACCESS_FILE:?one-run IaC access required}"
: "${RUNNER_TEMP:?private runner directory required}"
: "${GITHUB_RUN_ID:?GitHub run required}"
: "${GITHUB_RUN_ATTEMPT:?GitHub attempt required}"
: "${GITOPS_CHECKOUT:?fixed GitOps checkout required}"
: "${GITOPS_COMMIT:?fixed GitOps commit required}"
: "${NATIVE_RECEIPT_FILE:?same-run sanitized receipt path required}"
[[ "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ && "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]]
[[ "$GITOPS_COMMIT" =~ ^[0-9a-f]{40}$ && "$EXPECTED_CMDB_SHA256" =~ ^[0-9a-f]{64}$ ]]
expected_dir="$RUNNER_TEMP/prod-native-access-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
[[ "$NATIVE_ACCESS_FILE" == "$expected_dir/access.json" ]] || exit 2
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-native-standby-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]] || {
  echo 'Receipt must be a fresh same-run output.' >&2; exit 2;
}
export CMDB_FILE="$CMDB_DIR/cmdb.json"
[[ "$(sha256sum "$CMDB_FILE" | cut -d ' ' -f 1)" == "$EXPECTED_CMDB_SHA256" ]] || {
  echo 'CMDB differs from the accepted resource artifact.' >&2; exit 1;
}
ip="$(jq -er '.["web-saas-prod"].ip' "$CMDB_FILE")"
user="$(jq -er '.["web-saas-prod"].ansible_user' "$CMDB_FILE")"
jq -e --arg ip "$ip" --arg user "$user" --arg dir "$expected_dir" '
  .instance == "web-saas-prod" and .project == "open-platform-prod" and
  .target_ip == $ip and .ssh_user == $user and .private_key == ($dir + "/id_ed25519")
' "$NATIVE_ACCESS_FILE" >/dev/null || { echo 'Same-run access differs from trusted CMDB.' >&2; exit 1; }
test -s "$expected_dir/id_ed25519"
test -s "$CMDB_DIR/inventory.ini"
export ANSIBLE_SSH_COMMON_ARGS="-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$expected_dir/known_hosts"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" setup-web-saas-native-standby.yml
test -s "$NATIVE_RECEIPT_FILE"
jq -e --arg commit "$GITOPS_COMMIT" '.stage == "database_standby" and .host == "web-saas-prod" and
  .environment == "prod" and .writers_paused == true and
  .gitops_commit == $commit and .postgres_major == 17 and .independent_disk_verified == true and
  .schema_initialized == false and .database_cutover_approved == false' "$NATIVE_RECEIPT_FILE" >/dev/null
