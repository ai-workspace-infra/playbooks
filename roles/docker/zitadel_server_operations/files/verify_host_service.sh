#!/usr/bin/env bash
set -euo pipefail

# Post-deploy health of the ZITADEL service on its IAM host. Read-only: it
# never restarts, reloads or reconfigures anything, and it only checks that the
# Login client PAT exists, never reads it.
: "${ZITADEL_DOMAIN:?ZITADEL_DOMAIN is required}"
: "${ZITADEL_WORKSPACE:?ZITADEL_WORKSPACE is required}"
: "${ZITADEL_API_PORT:?ZITADEL_API_PORT is required}"
timeout="${ZITADEL_READY_TIMEOUT_SECONDS:-180}"
poll="${ZITADEL_READY_POLL_SECONDS:-5}"

[[ "${ZITADEL_DOMAIN}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || {
  echo 'Invalid ZITADEL domain.' >&2; exit 2;
}
[[ "${ZITADEL_WORKSPACE}" == /* ]] || { echo 'ZITADEL_WORKSPACE must be an absolute path.' >&2; exit 2; }
for value in "${ZITADEL_API_PORT}" "${timeout}" "${poll}"; do
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || { echo 'Port and readiness budget must be positive integers.' >&2; exit 2; }
done

deadline=$((SECONDS + timeout))
wait_or_fail() { # <message>
  if ((SECONDS >= deadline)); then
    echo "$1" >&2
    exit 1
  fi
  sleep "${poll}"
}

discovery_matches() {
  python3 -c '
import json, sys
issuer = "https://" + sys.argv[1]
try:
    document = json.loads(sys.stdin.read())
except ValueError:
    sys.exit(1)
ok = document.get("issuer") == issuer and str(document.get("jwks_uri", "")).startswith(issuer + "/")
sys.exit(0 if ok else 1)
' "${ZITADEL_DOMAIN}"
}

# 1. The GitOps-reconciled API and Login containers report healthy.
for service in zitadel login; do
  until [[ "$(docker inspect --format '{{.State.Health.Status}}' "shared-zitadel-${service}-1" 2>/dev/null)" == healthy ]]; do
    wait_or_fail "ZITADEL ${service} container is not healthy."
  done
done

# 2. The first-instance bootstrap completed: it writes the Login client PAT.
[[ -s "${ZITADEL_WORKSPACE}/login-client.pat" ]] || {
  echo 'Login client PAT is missing: the ZITADEL first-instance bootstrap did not complete.' >&2
  exit 1
}

# 3. Caddy, the public entry, is running.
systemctl is-active --quiet caddy || { echo 'Caddy is not active on the IAM host.' >&2; exit 1; }

# 4. The API answers on its loopback port.
until curl --fail --silent --show-error --output /dev/null --max-time 10 \
    -H "Host: ${ZITADEL_DOMAIN}" -H 'X-Forwarded-Proto: https' \
    "http://127.0.0.1:${ZITADEL_API_PORT}/.well-known/openid-configuration"; do
  wait_or_fail "ZITADEL API does not answer on 127.0.0.1:${ZITADEL_API_PORT}."
done

# 5. Caddy serves verified TLS discovery for the domain on this host. The
# certificate may still be issuing right after a first deploy, so poll.
until body="$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    --resolve "${ZITADEL_DOMAIN}:443:127.0.0.1" \
    "https://${ZITADEL_DOMAIN}/.well-known/openid-configuration")" \
    && discovery_matches <<<"${body}"; do
  wait_or_fail "Caddy on the IAM host does not serve verified TLS OIDC discovery for ${ZITADEL_DOMAIN}."
done

echo "ZITADEL host service verified for ${ZITADEL_DOMAIN}: API and Login healthy, bootstrap complete, Caddy TLS discovery valid."
