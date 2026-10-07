#!/usr/bin/env bash
set -euo pipefail

: "${REPLACEMENT_IP:?REPLACEMENT_IP is required}"
: "${TARGET_DOMAIN:?TARGET_DOMAIN is required}"

for attempt in $(seq 1 30); do
  if curl -fsS --max-time 10 --resolve "${TARGET_DOMAIN}:443:${REPLACEMENT_IP}" \
      "https://${TARGET_DOMAIN}/" >/dev/null; then
    echo "Replacement health check passed (${attempt}/30)"
    exit 0
  fi
  echo "Waiting for replacement ${REPLACEMENT_IP} (${attempt}/30)"
  sleep 10
done

echo "::error::Replacement health check failed" >&2
exit 1
