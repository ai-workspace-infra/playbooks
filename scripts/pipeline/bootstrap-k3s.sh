#!/usr/bin/env bash
# Owner: Playbooks. Caller: bootstrap-node.sh or an authorized deployment action.
set -euo pipefail
: "${MATRIX_HOST:?}"
: "${K3S_GITOPS_CHECKOUT:?}"
: "${K3S_GITOPS_SHA:?}"
: "${K3S_RESOURCE_MANIFEST:?}"
: "${K3S_BOOTSTRAP_RECEIPT:?}"
: "${K3S_CMDB_SHA256:?}"
cmdb_file="${CMDB_FILE:-../cmdb/cmdb.json}"
[[ "$K3S_GITOPS_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$(git -C "$K3S_GITOPS_CHECKOUT" rev-parse HEAD)" == "$K3S_GITOPS_SHA" ]] || exit 2
[[ -z "$(git -C "$K3S_GITOPS_CHECKOUT" status --porcelain --untracked-files=all)" ]] || exit 2
[[ "$(sha256sum "$cmdb_file" | cut -d ' ' -f 1)" == "$K3S_CMDB_SHA256" ]] || exit 2
checkout="$(realpath "$K3S_GITOPS_CHECKOUT")"
manifest="$(realpath "$checkout/$K3S_RESOURCE_MANIFEST")"
[[ "$manifest" == "$checkout/"* && -f "$manifest" ]] || exit 2
jq -e --arg host "$MATRIX_HOST" '.[$host] | type == "object" and (.ip | type == "string")' "$cmdb_file" >/dev/null
[[ ! -e "$K3S_BOOTSTRAP_RECEIPT" ]] || { echo "Receipt path must be fresh for this run" >&2; exit 2; }
export AI_WORKSPACE_CMDB_JSON="$(realpath "$cmdb_file")"
export ANSIBLE_ROLES_PATH="$(pwd)/roles"
params="$(mktemp)"
trap 'rm -f "$params"' EXIT
jq -n --arg manifest "$manifest" --arg checkout "$checkout" --arg sha "$K3S_GITOPS_SHA" --arg receipt "$K3S_BOOTSTRAP_RECEIPT" \
  '{bootstrap_manifest:$manifest,bootstrap_gitops_checkout:$checkout,bootstrap_gitops_sha:$sha,bootstrap_receipt:$receipt}' > "$params"
chmod 600 "$params"
ansible-inventory -i inventory/terraform_cmdb.py --host "$MATRIX_HOST" >/dev/null
ansible-playbook -i inventory/terraform_cmdb.py k3s_platform_bootstrap.yml --limit "$MATRIX_HOST" --extra-vars "@$params" --syntax-check
ansible -i inventory/terraform_cmdb.py "$MATRIX_HOST" -m ansible.builtin.ping
ansible-playbook -i inventory/terraform_cmdb.py k3s_platform_bootstrap.yml --limit "$MATRIX_HOST" --extra-vars "@$params"
jq -e --arg sha "$K3S_GITOPS_SHA" '.result == "FLUX_HANDOVER_VERIFIED" and .gitops_sha == $sha and .common.result == "COMMON_NODE_VERIFIED"' "$K3S_BOOTSTRAP_RECEIPT" >/dev/null
jq -e --arg sha "$K3S_GITOPS_SHA" '.result == "K3S_IDENTITIES_VERIFIED" and .rbac_revision == $sha and .principals == ["root", "operator", "auditor"]' "$K3S_BOOTSTRAP_RECEIPT.identities.json" >/dev/null
