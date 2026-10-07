#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_ENV:?TARGET_ENV is required}"
: "${K6_TEST_PROFILE:?K6_TEST_PROFILE is required}"
: "${LOAD_DURATION:?LOAD_DURATION is required}"
: "${LOAD_MAX_VUS:?LOAD_MAX_VUS is required}"
: "${K6_PROMETHEUS_RW_SERVER_URL:?K6_PROMETHEUS_RW_SERVER_URL is required}"
: "${K6_PROMETHEUS_RW_USERNAME:?K6_PROMETHEUS_RW_USERNAME is required}"
: "${K6_PROMETHEUS_RW_PASSWORD:?K6_PROMETHEUS_RW_PASSWORD is required}"

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
echo "Load selection duration=${LOAD_DURATION} max_vus=${LOAD_MAX_VUS}"

docker run --rm \
  --volume "${test_script}:/test/k6-load-test.js:ro" \
  --env TARGET_ENV \
  --env TARGET_BASE_URL \
  --env K6_TEST_PROFILE \
  --env LOAD_DURATION \
  --env LOAD_MAX_VUS \
  --env K6_TEST_ID \
  --env K6_PROMETHEUS_RW_SERVER_URL \
  --env K6_PROMETHEUS_RW_USERNAME \
  --env K6_PROMETHEUS_RW_PASSWORD \
  --env K6_PROMETHEUS_RW_TREND_STATS \
  --env K6_API_TOKEN \
  grafana/k6:1.7.1 \
  run \
  -o experimental-prometheus-rw \
  --tag testid="${K6_TEST_ID}" \
  /test/k6-load-test.js
