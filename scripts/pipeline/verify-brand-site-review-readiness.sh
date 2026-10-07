#!/usr/bin/env bash
# Post-deploy review-readiness check for the public brand website.
#
# Company / app-store reviewers (Google for Startups, Google Play, Apple
# Developer, Microsoft) crawl the brand domain and judge whether the company
# and product are real. This probes the deployed brand hosts the way a crawler
# would and fails when a page leaves the brand domain, is not served in place,
# or the identity facts are missing. Spec:
# ai-workspace-lab/xworkspace-core-skills -> engineering-standards/
# store-and-startup-homepage-spec.
set -euo pipefail

CONFIG_FILE="${CLOUDFLARE_BOUNDARY_CONFIG:?CLOUDFLARE_BOUNDARY_CONFIG must point to the rendered GitOps manifest}"
VERIFY_ATTEMPTS="${VERIFY_ATTEMPTS:-12}"
VERIFY_INTERVAL_SECONDS="${VERIFY_INTERVAL_SECONDS:-10}"
LEGAL_NAME="${BRAND_LEGAL_NAME:-XWork Technologies LLC}"
EMAIL_DOMAIN="${BRAND_EMAIL_DOMAIN:-xworktech.com}"
# Runner IPs are datacenter addresses and Cloudflare may challenge them even
# when the real crawler is allowed, so a challenge only warns unless strict.
STRICT="${BRAND_CHECK_STRICT:-false}"
BOT_UA='Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
PUBLIC_PATHS="${BRAND_PUBLIC_PATHS:-/ /about /contact /terms /privacy /support /products/xworkmate /products/xconnect /prices /download /docs /blogs}"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
test -f "${CONFIG_FILE}" || { echo "GitOps routing manifest not found: ${CONFIG_FILE}" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

failures=0
fail() { echo "::error::brand review-readiness: $*" >&2; failures=$((failures + 1)); }
warn() { echo "::warning::brand review-readiness: $*" >&2; }

# probe HOST PATH -> sets STATUS, LOCATION, BODY_FILE
probe() {
  local host="$1" path="$2" result
  BODY_FILE="${work}/body"
  # curl can emit a valid HTTP status through --write-out and still return a
  # non-zero code when a large response body times out. Preserve that status;
  # replacing it with a synthetic 000 creates a false readiness failure.
  result="$(curl --silent --show-error --max-time 20 --user-agent "${BOT_UA}" \
    --output "${BODY_FILE}" --write-out '%{http_code} %{redirect_url}' \
    "https://${host}${path}" 2>/dev/null || true)"
  if [[ "${result}" =~ ^([0-9]{3})[[:space:]](.*)$ ]]; then
    STATUS="${BASH_REMATCH[1]}"
    LOCATION="${BASH_REMATCH[2]}"
  else
    STATUS="000"
    LOCATION=""
  fi
}

is_challenge() {
  [[ "${STATUS}" == "403" || "${STATUS}" == "503" ]] || return 1
  grep -Eiq 'just a moment|cf-browser-verification|challenge-platform' "${BODY_FILE}" 2>/dev/null
}

challenge_note() {
  if [[ "${STRICT}" == "true" ]]; then
    fail "$1 served a bot challenge (HTTP ${STATUS})"
  else
    warn "$1 served a bot challenge to the runner (HTTP ${STATUS}); verify the crawler bypass rule"
  fi
}

check_host_once() {
  local host="$1" path
  failures=0

  for path in ${PUBLIC_PATHS}; do
    probe "${host}" "${path}"
    if [[ "${STATUS}" == 3* ]]; then
      case "${LOCATION}" in
        "https://${host}"*|"/"*) fail "https://${host}${path} redirects to ${LOCATION}; expected 200 in place" ;;
        *) fail "https://${host}${path} leaves ${host} (HTTP ${STATUS} -> ${LOCATION})" ;;
      esac
    elif [[ "${STATUS}" == "200" ]]; then
      :
    elif is_challenge; then
      challenge_note "https://${host}${path}"
    else
      fail "https://${host}${path} returned HTTP ${STATUS}; expected 200"
    fi
  done

  # Identity facts on the homepage and the contact surface.
  probe "${host}" "/"
  if [[ "${STATUS}" == "200" ]]; then
    grep -Fqi "${LEGAL_NAME}" "${BODY_FILE}" || fail "https://${host}/ does not contain the legal name '${LEGAL_NAME}'"
    if grep -Eiq '©[^<]{0,40}onwalk' "${BODY_FILE}"; then
      fail "https://${host}/ shows a legacy copyright owner"
    fi
  fi
  probe "${host}" "/contact"
  if [[ "${STATUS}" == "200" ]]; then
    if grep -Eiq '[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|qq|163|yahoo)\.[a-z]+' "${BODY_FILE}"; then
      fail "https://${host}/contact exposes a personal mailbox; use @${EMAIL_DOMAIN}"
    fi
    grep -Fqi "@${EMAIL_DOMAIN}" "${BODY_FILE}" || fail "https://${host}/contact has no @${EMAIL_DOMAIN} contact address"
  fi
  probe "${host}" "/support"
  if [[ "${STATUS}" == "200" ]] && grep -Eiq '[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|qq|163|yahoo)\.[a-z]+' "${BODY_FILE}"; then
    fail "https://${host}/support exposes a personal mailbox; use @${EMAIL_DOMAIN}"
  fi

  # Crawler metadata.
  probe "${host}" "/robots.txt"
  if is_challenge; then
    challenge_note "https://${host}/robots.txt"
  elif [[ "${STATUS}" != "200" ]]; then
    fail "https://${host}/robots.txt returned HTTP ${STATUS}"
  elif grep -Eiq '^Disallow:[[:space:]]*/[[:space:]]*$' "${BODY_FILE}"; then
    fail "https://${host}/robots.txt disallows the whole site"
  fi
  probe "${host}" "/sitemap.xml"
  if is_challenge; then
    challenge_note "https://${host}/sitemap.xml"
  elif [[ "${STATUS}" != "200" ]] || ! grep -Fq '<loc>' "${BODY_FILE}"; then
    fail "https://${host}/sitemap.xml is missing or has no <loc> entries (HTTP ${STATUS})"
  fi

  return "${failures}"
}

hosts="$(jq -r '.spec.serverless.frontend_router.website.hosts[]? // empty' "${CONFIG_FILE}")"
if [[ -z "${hosts}" ]]; then
  echo "No frontend_router.website.hosts declared; nothing to verify." >&2
  exit 0
fi

overall=0
while IFS= read -r host; do
  [[ -n "${host}" ]] || continue
  echo "Brand review-readiness: ${host}"
  attempt=1
  host_ok=true
  until check_host_once "${host}" 2>"${work}/errors"; do
    if (( attempt >= VERIFY_ATTEMPTS )); then
      cat "${work}/errors" >&2
      host_ok=false
      overall=1
      break
    fi
    attempt=$((attempt + 1))
    sleep "${VERIFY_INTERVAL_SECONDS}"
  done
  if [[ "${host_ok}" == true ]]; then
    # Surface warnings (for example a runner-side bot challenge) on success.
    grep -F '::warning::' "${work}/errors" >&2 || true
  fi

  # An apex host should also answer on www (the reviewer may type either).
  if [[ "${host}" != www.* ]] && command -v dig >/dev/null 2>&1; then
    if [[ -z "$(dig +short "www.${host}" | sed -n '1p')" ]]; then
      warn "www.${host} has no DNS record; add a www CNAME that redirects to ${host}"
    fi
  fi
done <<<"${hosts}"

if (( overall != 0 )); then
  echo "Brand review-readiness check failed. See the store-and-startup-homepage-spec skill (xworkspace-core-skills)." >&2
  exit 1
fi
echo "Brand review-readiness check passed."
