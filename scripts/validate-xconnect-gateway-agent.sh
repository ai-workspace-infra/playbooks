#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module_root="${repo_root}/tools/xconnect-gateway-agent"

if rg -n 'flush ruleset|systemctl|sudo|/bin/(ba)?sh|sh -c' "${module_root}" --glob '*.go' --glob '!**/*_test.go'; then
  echo "gateway Agent contains a forbidden broad or privileged command" >&2
  exit 1
fi

if rg -n '"syncconf"|"-f"|"rmi"|"adi"' "${module_root}/internal/gateway" \
  --glob '*.go' --glob '!apply.go' --glob '!xray.go' --glob '!**/*_test.go'; then
  echo "runtime mutation escaped the isolated transaction implementation" >&2
  exit 1
fi

rg -q 'Runner.Run\(ctx, binary, "show", interfaceName, "dump"\)' \
  "${module_root}/internal/gateway/wireguard.go"
rg -q 'cfg.RuntimeApplyEnabled\(\)' "${module_root}/cmd/xconnect-gateway-agent/main.go"
rg -q 'CapabilityBoundingSet=.*CAP_NET_ADMIN' "${repo_root}/roles/vhosts/xconnect-gateway/templates/xconnect-gateway-agent.service.j2"
rg -q 'CapabilityBoundingSet=\{% if' "${repo_root}/roles/vhosts/xconnect-gateway/templates/xconnect-gateway-agent.service.j2"
rg -q 'table inet xconnect_one' "${module_root}/internal/gateway/policy.go"
rg -q 'DisallowUnknownFields' "${module_root}/internal/gateway/config.go"

unformatted="$(gofmt -l "${module_root}")"
if [[ -n "${unformatted}" ]]; then
  echo "unformatted Go files:" >&2
  echo "${unformatted}" >&2
  exit 1
fi

echo "xconnect gateway Agent static guards passed"
