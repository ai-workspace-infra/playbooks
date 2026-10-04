#!/usr/bin/env bash
set -euo pipefail

: "${POST_DNS_DOMAIN:?POST_DNS_DOMAIN is required}"
: "${POST_DNS_TARGET_IP:?POST_DNS_TARGET_IP is required}"
: "${POST_DNS_RESTART_CADDY:?POST_DNS_RESTART_CADDY is required}"
: "${POST_DNS_HEALTH_PATH:?POST_DNS_HEALTH_PATH is required}"

python3 - "${POST_DNS_DOMAIN}" "${POST_DNS_TARGET_IP}" "${POST_DNS_HEALTH_PATH}" <<'PY'
import ipaddress, re, sys
domain, address, path = sys.argv[1:]
ipaddress.IPv4Address(address)
if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]", domain):
    raise SystemExit("Invalid post-cutover domain")
if not path.startswith("/") or any(c.isspace() or ord(c) < 32 for c in path):
    raise SystemExit("Invalid post-cutover health path")
PY

case "${POST_DNS_RESTART_CADDY}" in
  true)
    : "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH is required for Caddy restart}"
    : "${POST_DNS_SSH_USER:?POST_DNS_SSH_USER is required for Caddy restart}"
    [[ "${POST_DNS_SSH_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
      echo 'Invalid SSH user for Caddy restart.' >&2; exit 1;
    }
    ssh -i "${SSH_PRIVATE_KEY_PATH}" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15 \
      "${POST_DNS_SSH_USER}@${POST_DNS_TARGET_IP}" 'systemctl restart caddy'
    ;;
  false) ;;
  *) echo 'POST_DNS_RESTART_CADDY must be true or false.' >&2; exit 1 ;;
esac

for attempt in $(seq 1 36); do
  if code="$(curl --connect-timeout 5 --max-time 10 --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --resolve "${POST_DNS_DOMAIN}:443:${POST_DNS_TARGET_IP}" \
    "https://${POST_DNS_DOMAIN}${POST_DNS_HEALTH_PATH}" 2>/dev/null)"; then
    if [[ "${code}" == 200 ]]; then
      echo "Verified public TLS and service health on ${POST_DNS_DOMAIN} at ${POST_DNS_TARGET_IP}."
      exit 0
    fi
  fi
  ((attempt == 36)) || sleep 5
done
echo 'Post-cutover HTTPS health verification failed; the control workflow must restore DNS.' >&2
exit 1
