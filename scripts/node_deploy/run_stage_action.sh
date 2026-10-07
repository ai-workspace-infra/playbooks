#!/usr/bin/env bash
# Run the Vault-API part of a node stage (see stage_plan.py "action").
#
#   run_stage_action.sh ACTION CONTRACT
#
# cutover / remove-legacy call the Vault API from the runner with the stage's
# scoped token (VAULT_ADDR, VAULT_TOKEN in the environment). Anything that
# changes a host runs as a playbook tag in ai-workspace-infra/playbooks, not
# here.
set -euo pipefail

action="$1" contract="$2"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

legacy_id() { jq -er '.spec.nodes[] | select(.groups | index("vault_legacy_source")) | .id' "${contract}"; }
new_ids() { jq -r '[.spec.nodes[] | select((.groups | index("vault_legacy_source")) | not) | .id] | join(" ")' "${contract}"; }

case "${action}" in
  cutover)
    # shellcheck disable=SC2046
    python3 "${script_dir}/vault_raft_operator.py" step-down --legacy-id "$(legacy_id)" --expect $(new_ids)
    ;;
  remove-legacy)
    # shellcheck disable=SC2046
    python3 "${script_dir}/vault_raft_operator.py" remove-peer --legacy-id "$(legacy_id)" --expect $(new_ids)
    ;;
  *)
    echo "::error::unknown stage action ${action}" >&2
    exit 1
    ;;
esac
