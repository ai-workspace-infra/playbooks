#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${XCONNECT_DECLARATION_JSON:?}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
accounts="$(jq -er '.spec.zero.accounts_api_url' "$XCONNECT_DECLARATION_JSON")"
portal="$(jq -er '.spec.zero.portal_url' "$XCONNECT_DECLARATION_JSON")"
[[ "$accounts" =~ ^https://[A-Za-z0-9.-]+$ && "$portal" =~ ^https://[A-Za-z0-9.-]+/panel/xconnect-zero$ ]] || exit 2
probe() {
  local url="$1" routed="$2" status
  status="$(curl --silent --show-error --max-time 15 -H 'Accept: application/json' \
    -A 'XConnect-UAT-Lab/1.0' -D "$temporary/headers" -o "$temporary/body" -w '%{http_code}' "$url" 2>/dev/null)"
  [[ "$status" == 401 ]] || { echo 'Anonymous authentication rejection missing' >&2; return 1; }
  if [[ "$routed" == true ]]; then
    tr -d '\r' < "$temporary/headers" | grep -Eiq '^x-frontend-route:[[:space:]]*ssr-console[[:space:]]*$'
    jq -e '. == {error:"unauthenticated"}' "$temporary/body" >/dev/null
  fi
}
probe "$accounts/api/overlay/v1/admin/overview" false
for resource in overview networks devices invites; do probe "${portal%/panel/xconnect-zero}/api/xconnect-zero/$resource" true; done
echo 'PASS: anonymous Accounts boundary and Console Zero routes; signed-in acceptance remains separate.'
