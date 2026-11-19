#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_ENV:?TARGET_ENV is required}"
: "${K6_TEST_PROFILE:?K6_TEST_PROFILE is required}"
: "${LOAD_DURATION:?LOAD_DURATION is required}"
: "${LOAD_MAX_VUS:?LOAD_MAX_VUS is required}"

K6_DISABLE_PROMETHEUS_RW="${K6_DISABLE_PROMETHEUS_RW:-false}"
if [[ "${K6_DISABLE_PROMETHEUS_RW}" != "true" ]]; then
  : "${K6_PROMETHEUS_RW_SERVER_URL:?K6_PROMETHEUS_RW_SERVER_URL is required}"
  : "${K6_PROMETHEUS_RW_USERNAME:?K6_PROMETHEUS_RW_USERNAME is required}"
  : "${K6_PROMETHEUS_RW_PASSWORD:?K6_PROMETHEUS_RW_PASSWORD is required}"
fi

export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(90),p(95),p(99),avg,min,max}"
export K6_P95_THRESHOLD_MS="${K6_P95_THRESHOLD_MS:-}"
export K6_PROXY_TARGET_URL="${K6_PROXY_TARGET_URL:-}"
export K6_PROXY_URL="${K6_PROXY_URL:-}"
export K6_SUMMARY_EXPORT_PATH="${K6_SUMMARY_EXPORT_PATH:-}"

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_script="${workspace_dir}/roles/docker/observability-server/files/k6-load-test.js"

if [[ ! -f "${test_script}" ]]; then
  echo "::error::k6 test script not found: ${test_script}" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "::error::Docker is required to run the pinned k6 image." >&2
  exit 1
fi

K6_TEST_ID="${K6_TEST_ID:-k6-${TARGET_ENV}-$(date -u +%Y%m%dT%H%M%SZ)-${GITHUB_RUN_ID:-local}}"
export K6_TEST_ID

echo "Running k6 profile=${K6_TEST_PROFILE} environment=${TARGET_ENV} testid=${K6_TEST_ID}"
echo "Load selection duration=${LOAD_DURATION} max_vus=${LOAD_MAX_VUS} p95_threshold_ms=${K6_P95_THRESHOLD_MS:-default}"

docker_args=(
  run --rm
  --volume "${test_script}:/test/k6-load-test.js:ro"
  --env TARGET_ENV
  --env TARGET_BASE_URL
  --env K6_TEST_PROFILE
  --env LOAD_DURATION
  --env LOAD_MAX_VUS
  --env K6_TEST_ID
  --env K6_P95_THRESHOLD_MS
  --env K6_PROXY_TARGET_URL
  --env K6_API_TOKEN
)

if [[ -n "${K6_PROXY_URL}" ]]; then
  echo "Using upstream proxy=${K6_PROXY_URL}"
  docker_args+=(
    --add-host=host.docker.internal:host-gateway
    --env HTTPS_PROXY="${K6_PROXY_URL}"
    --env HTTP_PROXY="${K6_PROXY_URL}"
  )
fi

k6_run_args=(run --tag testid="${K6_TEST_ID}")

if [[ "${K6_DISABLE_PROMETHEUS_RW}" != "true" ]]; then
  docker_args+=(
    --env K6_PROMETHEUS_RW_SERVER_URL
    --env K6_PROMETHEUS_RW_USERNAME
    --env K6_PROMETHEUS_RW_PASSWORD
    --env K6_PROMETHEUS_RW_TREND_STATS
  )
  k6_run_args+=(-o experimental-prometheus-rw)
fi

if [[ -n "${K6_SUMMARY_EXPORT_PATH}" ]]; then
  summary_dir="$(cd "$(dirname "${K6_SUMMARY_EXPORT_PATH}")" && pwd)"
  summary_file="$(basename "${K6_SUMMARY_EXPORT_PATH}")"
  docker_args+=(--volume "${summary_dir}:/out")
  k6_run_args+=(--summary-export "/out/${summary_file}")
fi

docker "${docker_args[@]}" \
  grafana/k6:1.7.1 \
  "${k6_run_args[@]}" \
  /test/k6-load-test.js
