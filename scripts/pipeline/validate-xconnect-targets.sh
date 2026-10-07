#!/usr/bin/env bash
set -euo pipefail
# Targets are runtime IaC facts. This gate accepts no operator host overrides.
lab="${1:?Same-run private IaC directory required}"
expected="${2:?Expected run required}"
[[ "$expected" =~ ^xcl-[0-9]+-[0-9]+$ ]]
[[ "$(cat "$lab/run-id")" == "$expected" ]]
jq -e --arg run "$expected" --slurpfile variables "$lab/variables.json" '
  .resource_ids.value.run == $run and $variables[0].run_id == $run and
  (.gateway_provider.value == "external" or .gateway_provider.value == "aws-spot") and
  .resource_ids.value.gateway_source == .gateway_provider.value and
  .gateway_role.value == "relay" and .client_role.value == "controlled-client" and
  all(.gateway_ssh_user.value,.client_ssh_user.value;
    type == "string" and test("^[A-Za-z0-9][A-Za-z0-9_.-]*$"))
' "$lab/outputs.json" >/dev/null
if [[ "$(jq -er '.gateway_provider.value' "$lab/outputs.json")" == external ]]; then
  # A persistent external Gateway has no disposable AWS Gateway handoff.
  jq -e '.resource_ids.value.gateway == "external"' "$lab/outputs.json" >/dev/null
  exit 0
fi
handoff="$lab/desktop-public/desktop-handoff.json"
bash "$(dirname "${BASH_SOURCE[0]}")/validate-xconnect-handoff.sh" "$handoff"
jq -e --arg run "$expected" --slurpfile facts "$lab/outputs.json" \
  --slurpfile variables "$lab/variables.json" '
  $facts[0] as $facts |
  .run == $run and .expires_at == $variables[0].expires_at and
  .instances.gateway.instance_id == $facts.resource_ids.value.gateway and
  .instances.linux_one.instance_id == $facts.resource_ids.value.client and
  .instances.gateway.public_ip == $facts.gateway_ip.value and
  .instances.gateway.private_ip == $facts.gateway_private_ip.value and
  .instances.linux_one.public_ip == $facts.client_ip.value and
  .instances.linux_one.private_ip == $facts.client_private_ip.value and
  .gateway_endpoint.host == $facts.gateway_transport_ip.value
' "$handoff" >/dev/null
