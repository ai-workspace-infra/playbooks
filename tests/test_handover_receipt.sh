#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git init -q "$tmp/gitops"
git -C "$tmp/gitops" -c user.name=test -c user.email=test@example.invalid commit -q --allow-empty -m test
export MATRIX_HOST=node-a K3S_ENVIRONMENT=prod GITHUB_RUN_ID=123 GITHUB_RUN_ATTEMPT=1
export K3S_GITOPS_CHECKOUT="$tmp/gitops" K3S_GITOPS_SHA="$(git -C "$tmp/gitops" rev-parse HEAD)"
export CMDB_FILE="$tmp/cmdb.json" K3S_BOOTSTRAP_RECEIPT="$tmp/handover.json" K3S_RECEIPT_OUTPUT="$tmp/result.json"
printf '{"node-a":{"instance_id":42}}\n' > "$CMDB_FILE"
export K3S_CMDB_SHA256="$(sha256sum "$CMDB_FILE" | cut -d ' ' -f 1)"
jq -n --arg sha "$K3S_GITOPS_SHA" '{result:"FLUX_HANDOVER_VERIFIED",node:"node-a",gitops_sha:$sha,common:{result:"COMMON_NODE_VERIFIED",node:"node-a",cmdb_instance_id:"42"}}' > "$K3S_BOOTSTRAP_RECEIPT"
jq -n --arg sha "$K3S_GITOPS_SHA" '{result:"K3S_IDENTITIES_VERIFIED",rbac_revision:$sha,principals:["root","operator","auditor"]}' > "$K3S_BOOTSTRAP_RECEIPT.identities.json"
script="$root/roles/vhosts/k3s_platform_bootstrap/files/assemble-handover-receipt.sh"
bash "$script"
jq -e '.target == "node-a" and .instance_id == "42" and .result == "success" and .run_id == "123"' "$K3S_RECEIPT_OUTPUT" >/dev/null
rm "$K3S_RECEIPT_OUTPUT"
jq '.common.cmdb_instance_id="43"' "$K3S_BOOTSTRAP_RECEIPT" > "$tmp/bad" && mv "$tmp/bad" "$K3S_BOOTSTRAP_RECEIPT"
if bash "$script"; then echo 'mismatched instance accepted' >&2; exit 1; fi
[[ ! -e "$K3S_RECEIPT_OUTPUT" ]]
jq '.common.cmdb_instance_id="42"' "$K3S_BOOTSTRAP_RECEIPT" > "$tmp/good" && mv "$tmp/good" "$K3S_BOOTSTRAP_RECEIPT"
jq '.principals=["root"]' "$K3S_BOOTSTRAP_RECEIPT.identities.json" > "$tmp/bad" && mv "$tmp/bad" "$K3S_BOOTSTRAP_RECEIPT.identities.json"
if bash "$script"; then echo 'incomplete identities accepted' >&2; exit 1; fi
[[ ! -e "$K3S_RECEIPT_OUTPUT" ]]
