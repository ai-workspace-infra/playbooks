#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="${CLOUDFLARE_BOUNDARY_CONFIG:?CLOUDFLARE_BOUNDARY_CONFIG must point to the rendered GitOps manifest}"
VERIFY_ATTEMPTS="${VERIFY_ATTEMPTS:-18}"
VERIFY_INTERVAL_SECONDS="${VERIFY_INTERVAL_SECONDS:-10}"
serverless_dns_mode="${SERVERLESS_DNS_MODE:-none}"

case "${serverless_dns_mode}" in
  none|uat-records|prod-cutover)
    ;;
  *)
    echo "SERVERLESS_DNS_MODE must be one of: none, uat-records, prod-cutover" >&2
    exit 2
    ;;
esac

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v dig >/dev/null 2>&1 || { echo "dig is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
test -f "${CONFIG_FILE}" || { echo "GitOps routing manifest not found: ${CONFIG_FILE}" >&2; exit 1; }

is_success_status() {
  [[ "$1" =~ ^(200|301|302|307|308)$ ]]
}

is_cloudflare_challenge() {
  local headers="$1"
  grep -Eiq '^cf-mitigated:[[:space:]]*challenge' "${headers}"
}

is_cloudflare_challenge_text() {
  grep -Eiq '^cf-mitigated:[[:space:]]*challenge' <<<"$1"
}

is_cloudflare_edge_protection() {
  local status="$1" headers="$2"
  [[ "${status}" == "403" ]] || return 1
  # Cloudflare may omit cf-mitigated on a managed/WAF response. Require both
  # the Cloudflare server marker and Ray ID before treating that 403 as an
  # edge protection response; an origin/application 403 must still fail.
  if is_cloudflare_challenge_text "${headers}"; then
    return 0
  fi
  grep -Eiq '^server:[[:space:]]*cloudflare' <<<"${headers}" &&
    grep -Eiq '^cf-ray:[[:space:]]*[[:alnum:]-]+' <<<"${headers}"
}

alias_probe_is_acceptable() {
  local status="$1" headers="$2"
  # Custom-domain aliases serve the homepage in place. A redirect can hide
  # an incorrect host binding (for example www -> console) and must fail.
  [[ "${status}" == "200" ]] || {
    [[ "${status}" == "403" ]] && is_cloudflare_challenge "${headers}"
  }
}

environment="$(jq -er '.metadata.environment' "${CONFIG_FILE}")"
console_host="$(jq -er '.spec.serverless.console_host' "${CONFIG_FILE}")"
accounts_host="$(jq -er '.spec.serverless.accounts_host' "${CONFIG_FILE}")"
billing_host="$(jq -er '.spec.serverless.billing_host' "${CONFIG_FILE}")"
canonical_console="console.svc.plus"
canonical_accounts="accounts.svc.plus"
if [[ "${environment}" != "prod" ]]; then
  canonical_console="console-${environment}.onwalk.net"
  canonical_accounts="accounts-${environment}.onwalk.net"
fi

if [[ "${serverless_dns_mode}" != "none" ]]; then
  console_probe_host="${canonical_console}"
  accounts_probe_host="${canonical_accounts}"
else
  console_probe_host="${console_host}"
  accounts_probe_host="${accounts_host}"
fi
origin="https://${console_probe_host}"
api_origin="https://${accounts_probe_host}"
probe_root="$(mktemp -d)"
trap 'rm -rf "${probe_root}"' EXIT

for ((attempt = 1; attempt <= VERIFY_ATTEMPTS; attempt++)); do
  console_dns="$(dig +short @1.1.1.1 "${console_probe_host}" | sed -n '1p')"
  accounts_dns="$(dig +short @1.1.1.1 "${accounts_probe_host}" | sed -n '1p')"
  billing_dns="$(dig +short @1.1.1.1 "${billing_host}" | sed -n '1p')"
  console_headers="${probe_root}/console.headers"
  console_status="$(curl --silent --show-error --dump-header "${console_headers}" --output /dev/null --write-out '%{http_code}' --max-time 20 "${origin}/" || true)"
  aliases_ready=true
  while IFS= read -r console_alias; do
    [[ -n "${console_alias}" ]] || continue
    alias_dns="$(dig +short @1.1.1.1 "${console_alias}" | sed -n '1p')"
    alias_headers="${probe_root}/alias-${console_alias//[^A-Za-z0-9]/_}.headers"
    alias_status="$(curl --silent --show-error --dump-header "${alias_headers}" --output /dev/null --write-out '%{http_code}' --max-time 20 "https://${console_alias}/" || true)"
    if [[ -z "${alias_dns}" ]] || ! alias_probe_is_acceptable "${alias_status}" "${alias_headers}"; then
      echo "Alias homepage not ready: https://${console_alias}/ HTTP ${alias_status}; expected 200 without redirect (or Cloudflare challenge)" >&2
      aliases_ready=false
    fi
  done < <(jq -r '(.spec.serverless.console_aliases // []) + (.spec.serverless.frontend_router.website.hosts // []) | unique[]' "${CONFIG_FILE}")
  while IFS= read -r accounts_alias; do
    [[ -n "${accounts_alias}" ]] || continue
    alias_dns="$(dig +short @1.1.1.1 "${accounts_alias}" | sed -n '1p')"
    alias_headers="${probe_root}/accounts-alias-${accounts_alias//[^A-Za-z0-9]/_}.headers"
    # Accounts aliases are bound to the Edge Gateway Core, not directly to
    # the Accounts Cloud Run service. Validate the public API/CORS contract
    # at the alias instead of probing the service-only /healthz endpoint.
    alias_preflight="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 20 \
      --request OPTIONS "https://${accounts_alias}/api/v1/health" \
      --header "Origin: ${origin}" \
      --header 'Access-Control-Request-Method: GET' \
      --header 'Access-Control-Request-Headers: Authorization, Content-Type' || true)"
    alias_status="$(awk 'NR == 1 {print $2}' <<<"${alias_preflight}" | tr -d '\r')"
    alias_cors_ready=false
    if [[ "${alias_status}" == "204" ]] &&
       grep -Eiq '^access-control-allow-origin:' <<<"${alias_preflight}"; then
      alias_cors_ready=true
    elif is_cloudflare_edge_protection "${alias_status}" "${alias_preflight}" ||
         is_cloudflare_challenge_text "${alias_preflight}"; then
      # A managed Cloudflare challenge is an expected protected-edge response
      # in the runner; the origin contract cannot be observed through it.
      alias_cors_ready=true
    fi
    if [[ -z "${alias_dns}" ]] || [[ "${alias_cors_ready}" != true ]]; then
      echo "Accounts alias CORS preflight not ready: https://${accounts_alias}/api/v1/health HTTP ${alias_status}" >&2
      aliases_ready=false
    fi
  done < <(jq -r '.spec.serverless.accounts_aliases[]? // empty' "${CONFIG_FILE}")
  platform_origin="$(jq -r '.spec.serverless.frontend_router.website.platform_origin // empty' "${CONFIG_FILE}")"
  while IFS= read -r website_host; do
    [[ -n "${website_host}" ]] || continue
    for platform_path in '/login' '/ai-workspace?entry=trial'; do
      website_headers="${probe_root}/website.headers"
      website_status="$(curl --silent --show-error --dump-header "${website_headers}" --output /dev/null --write-out '%{http_code}' --max-time 20 "https://${website_host}${platform_path}" || true)"
      website_location="$(awk 'tolower($1) == "location:" {sub(/\r$/, "", $2); print $2}' "${website_headers}" | tail -1)"
      if [[ "${platform_path}" == /ai-workspace* ]]; then
        # Public workspace services stay on the requesting brand domain. Keep
        # this legacy query in the probe so retired trial links are verified
        # as same-origin public pages rather than as platform redirects.
        if [[ "${website_status}" != 200 || -n "${website_location}" ]]; then
          echo "Website public workspace boundary failed: ${website_host}${platform_path} HTTP ${website_status} Location=${website_location}" >&2
          aliases_ready=false
        fi
      elif [[ "${website_status}" != 302 || "${website_location}" != "${platform_origin}${platform_path}" ]]; then
        echo "Website platform boundary failed: ${website_host}${platform_path} HTTP ${website_status} Location=${website_location}" >&2
        aliases_ready=false
      fi
    done
  done < <(jq -r '.spec.serverless.frontend_router.website.hosts[]? // empty' "${CONFIG_FILE}")
  preflight_headers="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 20 \
    --request OPTIONS "${api_origin}/api/v1/health" \
    --header "Origin: ${origin}" \
    --header 'Access-Control-Request-Method: GET' \
    --header 'Access-Control-Request-Headers: Authorization, Content-Type' || true)"
  preflight_status="$(awk 'NR == 1 {print $2}' <<<"${preflight_headers}" | tr -d '\r')"
  # Billing is exposed through the Edge Gateway Core custom domain. Probe the
  # service readiness contract, which is implemented by the deployed Go
  # service, instead of assuming the generic accounts /healthz path exists.
  billing_headers="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 20 "https://${billing_host}/readyz" || true)"
  billing_status="$(awk 'NR == 1 {print $2}' <<<"${billing_headers}" | tr -d '\r')"
  billing_route="$(awk 'BEGIN { IGNORECASE=1 } /^X-Upstream-Route:/ {sub(/\r$/, "", $2); print $2}' <<<"${billing_headers}" | tail -1)"

  full_chain_ready=false
  if is_success_status "${console_status}" && [[ "${preflight_status}" == "204" &&
        "${billing_status}" == "200" && "${billing_route}" == "cloud-run-billing" &&
        "${preflight_headers}" =~ [Aa]ccess-[Cc]ontrol-[Aa]llow-[Oo]rigin: ]]; then
    full_chain_ready=true
  fi

  edge_challenge_ready=false
  preflight_edge_ready=false
  if [[ "${preflight_status}" == "204" &&
        "${preflight_headers}" =~ [Aa]ccess-[Cc]ontrol-[Aa]llow-[Oo]rigin: ]]; then
    preflight_edge_ready=true
  elif is_cloudflare_edge_protection "${preflight_status}" "${preflight_headers}"; then
    preflight_edge_ready=true
  fi
  if is_cloudflare_edge_protection "${console_status}" "$(<"${console_headers}")" &&
     is_cloudflare_edge_protection "${billing_status}" "${billing_headers}" &&
     [[ "${preflight_edge_ready}" == true ]]; then
    edge_challenge_ready=true
  fi

  if [[ -n "${console_dns}" && -n "${accounts_dns}" && -n "${billing_dns}" &&
        "${aliases_ready}" == true &&
        ( "${full_chain_ready}" == true || "${edge_challenge_ready}" == true ) ]]; then
    if [[ "${full_chain_ready}" == true ]]; then
      echo "Serverless public chain verified: Console=${console_probe_host}, Accounts=${accounts_probe_host}, Billing=${billing_host} via ${billing_route}; CORS preflight 204 (dns_mode=${serverless_dns_mode})"
    else
      echo "Serverless edge chain verified behind Cloudflare challenge: Console=${console_probe_host}, Accounts=${accounts_probe_host}, Billing=${billing_host}; DNS and protected edge responses are ready (dns_mode=${serverless_dns_mode})"
    fi
    exit 0
  fi

  echo "Waiting for serverless public chain (${attempt}/${VERIFY_ATTEMPTS}): console=${console_probe_host} dns=${console_dns:-missing}, aliases_ready=${aliases_ready}, accounts=${accounts_probe_host} dns=${accounts_dns:-missing}, billing=${billing_host} dns=${billing_dns:-missing}, console_http=${console_status}, preflight=${preflight_status:-missing}, billing_http=${billing_status:-missing}, billing_route=${billing_route:-missing}" >&2
  if (( attempt < VERIFY_ATTEMPTS )); then
    sleep "${VERIFY_INTERVAL_SECONDS}"
  fi
done

echo "Serverless public chain verification failed for ${console_probe_host} and ${accounts_probe_host}." >&2
exit 1
