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

python3 "${validator}" \
  --config "${fixture_root}/gateway.json" \
  --provider "${fixture_root}/provider.json" \
  --snapshot "${fixture_root}/snapshot.json"

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

rg -q 'CapabilityBoundingSet=$' "${role_root}/templates/xconnect-gateway-agent.service.j2"
rg -q 'apply_runtime.*false' "${fixture_root}/provider.json"
echo "xconnect gateway role contract checks passed"
