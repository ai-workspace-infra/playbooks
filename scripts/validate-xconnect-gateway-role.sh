#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
role_root="${repo_root}/roles/vhosts/xconnect-gateway"
fixture_root="${repo_root}/tests/fixtures/xconnect-gateway"
validator="${role_root}/files/xconnect-gateway-validate.py"
temp_dir="$(mktemp -d /tmp/xconnect-gateway-contract.XXXXXX)"
trap 'rm -rf "${temp_dir}"' EXIT

python3 -m json.tool "${role_root}/files/contracts/gateway-provider-manifest.schema.json" >/dev/null
python3 -m json.tool "${role_root}/files/contracts/gateway-snapshot.schema.json" >/dev/null
python3 -m json.tool "${fixture_root}/gateway.json" >/dev/null
python3 -m json.tool "${fixture_root}/provider.json" >/dev/null
python3 -m json.tool "${fixture_root}/snapshot.json" >/dev/null
python3 -m json.tool "${fixture_root}/snapshot-empty-peers.json" >/dev/null
python3 -m json.tool "${fixture_root}/gateway-apply.json" >/dev/null
python3 -m json.tool "${fixture_root}/provider-apply.json" >/dev/null
python3 -m json.tool "${fixture_root}/xray-base.json" >/dev/null
python3 -m json.tool "${fixture_root}/xray-baseline.json" >/dev/null

python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${fixture_root}/provider.json" \
  --snapshot "${fixture_root}/snapshot.json"

python3 "${validator}" \
  --config "${fixture_root}/gateway-apply.json" \
  --provider "${fixture_root}/provider-apply.json" \
  --xray-base "${fixture_root}/xray-base.json" \
  --xray-baseline "${fixture_root}/xray-baseline.json"

if python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${fixture_root}/provider.json" \
  --snapshot "${fixture_root}/snapshot-empty-peers.json" >/dev/null 2>&1; then
  echo "validator accepted empty peers without a safety override" >&2
  exit 1
fi

sed 's/"allow_empty_peers": false/"allow_empty_peers": true/' \
  "${fixture_root}/snapshot-empty-peers.json" >"${temp_dir}/allowed-empty-peers.json"
python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${fixture_root}/provider.json" \
  --snapshot "${temp_dir}/allowed-empty-peers.json" >/dev/null

sed 's/"expected_previous_generation": 41/"expected_previous_generation": 42/' \
  "${fixture_root}/snapshot.json" >"${temp_dir}/stale-generation.json"
if python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${fixture_root}/provider.json" \
  --snapshot "${temp_dir}/stale-generation.json" >/dev/null 2>&1; then
  echo "validator accepted a non-advancing snapshot generation" >&2
  exit 1
fi

sed 's/"proxy_core": "xray"/"proxy_core": "unsupported"/' \
  "${fixture_root}/gateway.json" >"${temp_dir}/bad-core.json"
if python3 "${validator}" \
  --config "${temp_dir}/bad-core.json" \
  --provider "${fixture_root}/provider.json" >/dev/null 2>&1; then
  echo "validator accepted an unsupported proxy core" >&2
  exit 1
fi

sed 's/"apply_runtime": false/"apply_runtime": true/' \
  "${fixture_root}/provider.json" >"${temp_dir}/bad-permissions.json"
if python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${temp_dir}/bad-permissions.json" >/dev/null 2>&1; then
  echo "validator accepted runtime apply in shadow mode" >&2
  exit 1
fi

if rg -n --ignore-case 'sing[-_ ]?box' \
  "${role_root}/defaults" \
  "${role_root}/tasks" \
  "${role_root}/handlers" \
  "${role_root}/templates" \
  "${role_root}/meta"; then
  echo "unsupported proxy runtime found in operational role paths" >&2
  exit 1
fi

rg -Fq 'CapabilityBoundingSet={% if xconnect_gateway_runtime_apply_enabled' "${role_root}/templates/xconnect-gateway-agent.service.j2"
rg -Fq 'AmbientCapabilities={% if xconnect_gateway_runtime_apply_enabled' "${role_root}/templates/xconnect-gateway-agent.service.j2"
rg -Fq -- '--mode {{' "${role_root}/templates/xconnect-gateway-agent.service.j2"
rg -Fq 'After=network-online.target {{ xconnect_gateway_xray_apply_service_name' "${role_root}/templates/xconnect-gateway-agent.service.j2"
rg -q 'apply_runtime.*false' "${fixture_root}/provider.json"
rg -q '"CAP_NET_ADMIN"' "${fixture_root}/provider-apply.json"
rg -q 'HandlerService' "${role_root}/templates/xray-base.json.j2"
rg -q 'xconnect-one-block' "${role_root}/templates/xray-base.json.j2"
rg -q '"network": "tcp"' "${repo_root}/roles/vhosts/xworkmate_bridge_distributed_vpn/templates/wireguard-over-vless.json.j2"
rg -q '"packetEncoding": "xudp"' "${repo_root}/roles/vhosts/xworkmate_bridge_distributed_vpn/templates/wireguard-over-vless.json.j2"
if rg -q '"allowInsecure"' "${repo_root}/roles/vhosts/xworkmate_bridge_distributed_vpn/templates/wireguard-over-vless.json.j2"; then
  echo "Xray 26.3.27 client template contains removed allowInsecure setting" >&2
  exit 1
fi
if rg -n '[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}' "${fixture_root}"; then
  echo "runtime relay credential leaked into a checked-in fixture" >&2
  exit 1
fi
rg -q 'runtime_apply_enabled' "${role_root}/tasks/deploy.yml"
rg -q '^  rescue:$' "${role_root}/tasks/deploy.yml"
rg -q 'Restore previous XConnect gateway service state' "${role_root}/tasks/deploy.yml"
rg -q 'Restore previous managed node credential after failed health' "${role_root}/tasks/deploy.yml"
rg -q 'Restore previous snapshot signing key after failed health' "${role_root}/tasks/deploy.yml"
rg -q 'checksum.*sha256' "${role_root}/tasks/deploy.yml"
rg -q "xconnect_gateway_restart_required" "${role_root}/tasks/deploy.yml"
if rg -n 'state: restarted' "${role_root}/tasks/deploy.yml"; then
  echo "gateway role contains an unconditional Agent restart" >&2
  exit 1
fi
if rg -n 'EnvironmentFile=' "${role_root}/templates/xconnect-gateway-agent.service.j2"; then
  echo "credential secret must be read from the protected file reference, not systemd environment" >&2
  exit 1
fi
if rg -n 'wg (set|syncconf)|nft (add|delete|flush|insert|replace)' \
  "${role_root}/tasks" \
  "${role_root}/templates"; then
  echo "shadow role contains a forbidden WireGuard or nftables write path" >&2
  exit 1
fi
echo "xconnect gateway role contract checks passed"
