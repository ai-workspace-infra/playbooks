#!/usr/bin/env bash
set -euo pipefail

# Public OIDC discovery for one ZITADEL domain, exactly as relying parties see
# it: the issuer must be the declared domain and its keys must be served there.
: "${ZITADEL_DOMAIN:?ZITADEL_DOMAIN is required}"
[[ "${ZITADEL_DOMAIN}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || {
  echo 'Invalid ZITADEL domain.' >&2
  exit 2
}

body="$(curl --fail --silent --show-error --retry 6 --retry-all-errors --retry-delay 5 \
  --connect-timeout 5 --max-time 20 "https://${ZITADEL_DOMAIN}/.well-known/openid-configuration")" || {
  echo "Public OIDC discovery for ${ZITADEL_DOMAIN} is unreachable." >&2
  exit 1
}
python3 -c '
import json, sys
issuer = "https://" + sys.argv[1]
try:
    document = json.loads(sys.stdin.read())
except ValueError:
    sys.exit(1)
ok = document.get("issuer") == issuer and str(document.get("jwks_uri", "")).startswith(issuer + "/")
sys.exit(0 if ok else 1)
' "${ZITADEL_DOMAIN}" <<<"${body}" || {
  echo "Public OIDC discovery for ${ZITADEL_DOMAIN} does not name issuer https://${ZITADEL_DOMAIN}." >&2
  exit 1
}
echo "ZITADEL OIDC discovery verified for ${ZITADEL_DOMAIN}"
