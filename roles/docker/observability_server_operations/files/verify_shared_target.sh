#!/usr/bin/env bash
set -euo pipefail
: "${TARGET_IP:?TARGET_IP is required}"
domain='observability.svc.plus'

curl --fail --silent --show-error --retry 4 --retry-all-errors --retry-delay 2 \
  --connect-timeout 5 --max-time 20 --insecure \
  --resolve "${domain}:443:${TARGET_IP}" \
  "https://${domain}/grafana/api/health" | jq -e '.database == "ok"' >/dev/null
for service in victoriametrics victorialogs victoriatraces; do
  curl --fail --silent --show-error --retry 4 --retry-all-errors --retry-delay 2 \
    --connect-timeout 5 --max-time 20 --insecure \
    --resolve "${domain}:443:${TARGET_IP}" \
    "https://${domain}/${service}/metrics" >/dev/null
done

summary="Shared GCP Observability target ${TARGET_IP} passed HTTPS health checks before DNS cutover."
printf '%s\n' "${summary}"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then printf '%s\n' "${summary}" >> "${GITHUB_STEP_SUMMARY}"; fi
