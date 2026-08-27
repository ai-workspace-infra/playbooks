#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module_root="${repo_root}/tools/xconnect-gateway-agent"

if find "${module_root}" -type f \( -name '*.go' -o -name 'go.mod' \) -print0 |
  xargs -0 grep -nE '(^|[[:space:]])(wg[[:space:]]+(set|syncconf)|nft[[:space:]]+(add|delete|flush|insert|replace))'; then
  echo "gateway Agent contains a forbidden data-plane write command" >&2
  exit 1
fi

if rg -n 'CommandContext\([^\n]*(set|syncconf|nft)|\.Run\([^\n]*(set|syncconf|nft)' \
  "${module_root}" --glob '*.go' --glob '!**/*_test.go'; then
  echo "gateway Agent command execution is not shadow-only" >&2
  exit 1
fi

rg -q 'Runner.Run\(ctx, binary, "show", interfaceName, "dump"\)' \
  "${module_root}/internal/gateway/wireguard.go"
rg -q 'RuntimeApplyEnabled:\s+false' "${module_root}/internal/gateway/agent.go"
rg -q 'AppliedGeneration:\s+0' "${module_root}/internal/gateway/agent.go"
rg -q 'DisallowUnknownFields' "${module_root}/internal/gateway/config.go"

unformatted="$(gofmt -l "${module_root}")"
if [[ -n "${unformatted}" ]]; then
  echo "unformatted Go files:" >&2
  echo "${unformatted}" >&2
  exit 1
fi

echo "xconnect gateway Agent static guards passed"
