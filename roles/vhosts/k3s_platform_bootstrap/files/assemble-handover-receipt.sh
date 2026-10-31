#!/usr/bin/env bash
# Assemble verified owner evidence for one target; no host operations or secret values.
set -euo pipefail
: "${MATRIX_HOST:?}" "${K3S_CMDB_SHA256:?}" "${CMDB_FILE:?}" "${K3S_GITOPS_SHA:?}" "${K3S_GITOPS_CHECKOUT:?}" "${K3S_BOOTSTRAP_RECEIPT:?}" "${K3S_RECEIPT_OUTPUT:?}" "${K3S_ENVIRONMENT:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
[[ "$K3S_GITOPS_SHA" =~ ^[0-9a-f]{40}$ && "$K3S_CMDB_SHA256" =~ ^[0-9a-f]{64}$ && "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ && "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || exit 2
[[ "$(git -C "$K3S_GITOPS_CHECKOUT" rev-parse HEAD)" == "$K3S_GITOPS_SHA" ]] || exit 2
[[ "$(sha256sum "$CMDB_FILE" | cut -d ' ' -f 1)" == "$K3S_CMDB_SHA256" ]] || exit 2
owner_sha="$(git rev-parse HEAD)"
[[ "$owner_sha" =~ ^[0-9a-f]{40}$ && ! -e "$K3S_RECEIPT_OUTPUT" ]] || exit 2
instance_id="$(jq -er --arg host "$MATRIX_HOST" '.[$host].instance_id | select(. != null) | tostring | select(test("^[1-9][0-9]*$"))' "$CMDB_FILE")"
jq -e --arg host "$MATRIX_HOST" --arg sha "$K3S_GITOPS_SHA" --arg instance "$instance_id" '.result == "FLUX_HANDOVER_VERIFIED" and .node == $host and .gitops_sha == $sha and .common.result == "COMMON_NODE_VERIFIED" and .common.node == $host and (.common.cmdb_instance_id | tostring) == $instance' "$K3S_BOOTSTRAP_RECEIPT" >/dev/null
jq -e --arg sha "$K3S_GITOPS_SHA" '.result == "K3S_IDENTITIES_VERIFIED" and .rbac_revision == $sha and .principals == ["root","operator","auditor"]' "$K3S_BOOTSTRAP_RECEIPT.identities.json" >/dev/null
umask 077
jq -n --arg target "$MATRIX_HOST" --arg environment "$K3S_ENVIRONMENT" --arg gitops_sha "$K3S_GITOPS_SHA" --arg playbooks_sha "$owner_sha" --arg cmdb_sha256 "$K3S_CMDB_SHA256" --arg run_id "$GITHUB_RUN_ID" --argjson run_attempt "$GITHUB_RUN_ATTEMPT" --arg instance_id "$instance_id" --slurpfile handover "$K3S_BOOTSTRAP_RECEIPT" --slurpfile identities "$K3S_BOOTSTRAP_RECEIPT.identities.json" '{schema_version:1,target:$target,environment:$environment,gitops_sha:$gitops_sha,playbooks_sha:$playbooks_sha,cmdb_sha256:$cmdb_sha256,run_id:$run_id,run_attempt:$run_attempt,instance_id:$instance_id,result:"success",handover:{result:$handover[0].result,node:$handover[0].node,gitops_sha:$handover[0].gitops_sha,common:{result:$handover[0].common.result}},identities:{result:$identities[0].result,rbac_revision:$identities[0].rbac_revision,principals:$identities[0].principals}}' > "$K3S_RECEIPT_OUTPUT"
