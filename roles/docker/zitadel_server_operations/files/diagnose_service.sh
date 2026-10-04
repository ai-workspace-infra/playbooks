#!/usr/bin/env bash
set -uo pipefail

# Read-only evidence after a failed ZITADEL verification: no restart, reload or
# configuration change. Every section runs even when an earlier one fails.
: "${ZITADEL_DOMAIN:?ZITADEL_DOMAIN is required}"
: "${ZITADEL_WORKSPACE:?ZITADEL_WORKSPACE is required}"
: "${ZITADEL_API_PORT:?ZITADEL_API_PORT is required}"
: "${ZITADEL_CADDY_CONF_DIR:?ZITADEL_CADDY_CONF_DIR is required}"
[[ "${ZITADEL_DOMAIN}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || {
  echo 'Invalid ZITADEL domain.' >&2; exit 2;
}
[[ "${ZITADEL_API_PORT}" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid ZITADEL API port.' >&2; exit 2; }

redact() {
  sed -E \
    -e 's/Bearer[[:space:]]+[^[:space:]]+/Bearer ***REDACTED***/g' \
    -e 's/gh[pousr]_[A-Za-z0-9_]+/***REDACTED_GITHUB_TOKEN***/g' \
    -e 's/hvs\.[A-Za-z0-9]+/***REDACTED_VAULT_TOKEN***/g'
}

echo '== ZITADEL stack containers'
docker ps --all --filter label=com.docker.compose.project=shared-zitadel \
  --format '{{.Names}} {{.Image}} {{.Status}}'
for service in zitadel login; do
  echo "== ${service} health probe"
  docker inspect --format '{{json .State.Health}}' "shared-zitadel-${service}-1" | redact
done
echo '== Login client PAT present'
if [[ -s "${ZITADEL_WORKSPACE}/login-client.pat" ]]; then echo yes; else echo no; fi
echo '== caddy service'
systemctl is-active caddy
systemctl show caddy -p ActiveState,SubState,NRestarts,ExecMainStatus
echo '== listeners :80/:443'
ss -ltnp '( sport = :80 or sport = :443 )'
echo '== caddy config'
ls -l "${ZITADEL_CADDY_CONF_DIR}"
echo '== local HTTPS via Caddy'
curl -sk -o /dev/null -w '%{http_code}\n' --max-time 10 --resolve "${ZITADEL_DOMAIN}:443:127.0.0.1" \
  "https://${ZITADEL_DOMAIN}/.well-known/openid-configuration"
echo '== local API'
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 -H "Host: ${ZITADEL_DOMAIN}" \
  "http://127.0.0.1:${ZITADEL_API_PORT}/.well-known/openid-configuration"
echo '== caddy journal'
journalctl -u caddy -n 60 --no-pager | redact
exit 0
