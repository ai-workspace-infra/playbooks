#!/usr/bin/env bash
# Shared controller access assertions only; IaC owns all cloud actions.
: "${CMDB_DIR:?resource artifact directory required}"
: "${EXPECTED_CMDB_SHA256:?reviewed CMDB checksum required}"
: "${NATIVE_ACCESS_FILE:?one-run IaC access required}"
: "${RUNNER_TEMP:?private runner directory required}"
: "${GITHUB_RUN_ID:?GitHub run required}"
: "${GITHUB_RUN_ATTEMPT:?GitHub attempt required}"
: "${GITOPS_CHECKOUT:?fixed GitOps checkout required}"
: "${GITOPS_COMMIT:?fixed GitOps commit required}"
[[ "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ && "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]]
[[ "$GITOPS_COMMIT" =~ ^[0-9a-f]{40}$ && "$EXPECTED_CMDB_SHA256" =~ ^[0-9a-f]{64}$ ]]
expected_dir="$RUNNER_TEMP/prod-native-access-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
[[ "$NATIVE_ACCESS_FILE" == "$expected_dir/access.json" ]] || exit 2
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
# Override legacy global SSH defaults. No multiplexed session may outlive the
# one-run cloud access owner's cleanup.
export ANSIBLE_HOST_KEY_CHECKING=true
export ANSIBLE_SSH_ARGS='-o ControlMaster=no -o ControlPersist=no'
