#!/usr/bin/env bash
set -euo pipefail
umask 077

playbooks_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:?}"
window="${NODE_OBSERVATION_WINDOW_MINUTES:-0}"
[[ "$window" =~ ^(10|20|until-expiry)$ ]] || { [[ "$window" == 0 ]] && exit 0; echo 'Invalid resolved node observation window' >&2; exit 1; }

gateway_provider=$(jq -er '.gateway_provider.value' "$LAB_DIR/outputs.json")
if [[ "$gateway_provider" == external ]]; then
  run_id=$(<"$LAB_DIR/run-id")
  expires_at=$(jq -er '.expires_at' "$LAB_DIR/variables.json")
  lease_deadline=$(jq -er '.expires_at | fromdateiso8601' "$LAB_DIR/variables.json")
  echo "NODE_OBSERVATION_OPEN run=$run_id minutes=$window lease_expires_at=$expires_at"
  while (( $(date +%s) < lease_deadline )); do
    remaining=$((lease_deadline - $(date +%s)))
    (( remaining > 30 )) && sleep 30 || sleep "$remaining"
  done
  echo 'NODE_OBSERVATION_RESULT=SUMMARY_ONLY external_gateway_persistent=true'
  exit 0
fi

handoff="$LAB_DIR/desktop-public/desktop-handoff.json"
test -f "$handoff" || { echo 'Public observation handoff is missing' >&2; exit 1; }
bash "$playbooks_root/scripts/pipeline/validate-xconnect-handoff.sh" "$handoff"

gateway=$(jq -er '.instances.gateway.public_ip' "$handoff")
gateway_user=$(jq -er '.gateway_ssh_user.value' "$LAB_DIR/outputs.json")
client=$(jq -er '.instances.linux_one.public_ip' "$handoff")
client_user=$(jq -er '.client_ssh_user.value' "$LAB_DIR/outputs.json")
run_id=$(jq -er '.run' "$handoff")
network_id=$(jq -er '.network_id' "$handoff")
gateway_id=$(jq -er '.gateway_id' "$handoff")
client_id="one-${run_id}"
gateway_public_key=$(jq -er '.gateway_public_key' "$handoff")
expires_at=$(jq -er '.expires_at' "$handoff")
lease_expires_at=$(jq -er '.expires_at' "$LAB_DIR/variables.json")
[[ "$expires_at" == "$lease_expires_at" ]] || { echo 'Public handoff expiry does not match the recorded lease expiry' >&2; exit 1; }
lease_deadline=$(jq -er '.expires_at | fromdateiso8601' "$LAB_DIR/variables.json")
now=$(date +%s)
end="$lease_deadline"
if [[ "$window" != until-expiry ]]; then
  candidate=$((now + window * 60))
  (( candidate < end )) && end="$candidate"
fi

echo "NODE_OBSERVATION_OPEN run=$run_id minutes=$window lease_expires_at=$expires_at"
if (( end <= now )); then
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=lease_expired local_independent_acceptance_required=true'
  exit 0
fi


observation_dir="$LAB_DIR/node-observation"
inventory="$observation_dir/inventory.ini"
variables="$observation_dir/variables.json"
mkdir -p "$observation_dir"
chmod 700 "$observation_dir"
trap 'rm -rf -- "$observation_dir"' EXIT
test -f "$playbooks_root/observability_operations.yml" || {
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=playbooks_observability_entrypoint_missing local_independent_acceptance_required=true'
  exit 0
}
command -v ansible-playbook >/dev/null 2>&1 || {
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=ansible_controller_missing local_independent_acceptance_required=true'
  exit 0
}
[[ "$gateway" =~ ^[0-9]+(\.[0-9]+){3}$ && "$client" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || {
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=invalid_observation_address local_independent_acceptance_required=true'
  exit 0
}
[[ "$gateway_user" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && "$client_user" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=invalid_observation_user local_independent_acceptance_required=true'
  exit 0
}
[[ "$gateway_public_key" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
  echo 'NODE_OBSERVATION_RESULT=UNVERIFIED reason=invalid_gateway_public_key local_independent_acceptance_required=true'
  exit 0
}

# The inventory and extra-vars file contain only public topology, explicit
# host selectors and paths. The SSH private key remains a runner-local file
# and is passed to Ansible by path; it is never serialized into either file.
printf '[xconnect_gateway]\n' > "$inventory"
printf "xconnect-gateway ansible_host=%s ansible_user=%s ansible_ssh_common_args='%s'\n\n" \
  "$gateway" "$gateway_user" "-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$LAB_DIR/known_hosts" >> "$inventory"
printf '[xconnect_client]\n' >> "$inventory"
printf "xconnect-one ansible_host=%s ansible_user=%s ansible_ssh_common_args='%s'\n" \
  "$client" "$client_user" "-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$LAB_DIR/known_hosts" >> "$inventory"
chmod 600 "$inventory"
jq -n \
  --arg environment uat \
  --arg operation xconnect_remote_observation \
  --arg gateway_host xconnect-gateway \
  --arg client_host xconnect-one \
  --arg run "$run_id" \
  --arg gateway_id "$gateway_id" \
  --arg client_id "$client_id" \
  --arg network_id "$network_id" \
  --arg gateway_public_key "$gateway_public_key" \
  '{observability_operations_environment:$environment,observability_operation:$operation,
    xconnect_remote_observation_gateway_host:$gateway_host,
    xconnect_remote_observation_client_host:$client_host,
    xconnect_remote_observation_run_id:$run,
    xconnect_remote_observation_gateway_id:$gateway_id,
    xconnect_remote_observation_client_id:$client_id,
    xconnect_remote_observation_network_id:$network_id,
    xconnect_remote_observation_gateway_public_key:$gateway_public_key,
    xconnect_remote_observation_gateway_state_dir:"/var/lib/xconnect-gateway",
    xconnect_remote_observation_client_state_dir:"/var/lib/xconnect-one",
    xconnect_remote_observation_become:true}' > "$variables"
chmod 600 "$variables"

observe_with_playbooks() {
  local remaining output summary
  remaining=$((end - $(date +%s)))
  (( remaining > 90 )) && remaining=90
  (( remaining > 0 )) || return 0
  if output=$(timeout "${remaining}s" ansible-playbook \
      -i "$inventory" \
      --private-key "$LAB_DIR/id_ed25519" \
      "$playbooks_root/observability_operations.yml" \
      -e "@$variables" 2>/dev/null); then
    summary=$(sed -n 's/.*\(NODE_OBSERVATION run=[^"]*SUMMARY_ONLY\).*/\1/p' <<<"$output" | tail -1 || true)
  else
    summary=''
  fi
  if [[ "$summary" =~ ^NODE_OBSERVATION\ run= ]]; then
    echo "$summary"
  else
    echo "NODE_OBSERVATION run=$run_id refresh=UNVERIFIED sync=UNVERIFIED gateway_peer=UNVERIFIED client_peer=UNVERIFIED SUMMARY_ONLY"
  fi
}

observe_nodes() {
  observe_with_playbooks
}

while (( $(date +%s) < end )); do
  observe_nodes
  now=$(date +%s)
  remaining=$((end - now))
  (( remaining <= 0 )) && break
  sleep_seconds=30
  (( remaining < sleep_seconds )) && sleep_seconds=$remaining
  sleep "$sleep_seconds"
done
echo 'NODE_OBSERVATION_RESULT=SUMMARY_ONLY local_independent_acceptance_required=true'
