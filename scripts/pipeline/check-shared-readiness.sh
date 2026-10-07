#!/usr/bin/env bash
set -euo pipefail

# Daily Snapshot consumes the independently managed Shared platform. This
# probe is read-only: it never authenticates to Vault, writes policy, runs
# Terraform, restarts services, or performs migration.

vault_endpoint="${SHARED_VAULT_ENDPOINT:?SHARED_VAULT_ENDPOINT must be set}"
observability_endpoint="${SHARED_OBSERVABILITY_ENDPOINT:?SHARED_OBSERVABILITY_ENDPOINT must be set}"
iam_endpoint="${SHARED_IAM_ENDPOINT:?SHARED_IAM_ENDPOINT must be set}"
expected_iam_issuer="${SHARED_IAM_ISSUER:?SHARED_IAM_ISSUER must be set}"
timeout_seconds="${SHARED_READINESS_TIMEOUT_SECONDS:-20}"

for endpoint in "${vault_endpoint}" "${observability_endpoint}" "${iam_endpoint}" "${expected_iam_issuer}"; do
  [[ "${endpoint}" =~ ^https://[^/]+$ ]] || {
    echo "::error::Shared readiness endpoints must be HTTPS origins without a path." >&2
    exit 2
  }
done
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "::error::SHARED_READINESS_TIMEOUT_SECONDS must be a positive integer." >&2
  exit 2
}

probe_json() {
  local label="$1" url="$2" body status
  body="$(mktemp)"
  trap 'rm -f "${body}"' RETURN
  status="$(curl --silent --show-error --connect-timeout "${timeout_seconds}" --max-time "${timeout_seconds}" \
    --output "${body}" --write-out '%{http_code}' "${url}" 2>/dev/null || true)"
  [[ "${status}" == 200 ]] || {
    echo "::error::Shared ${label} readiness returned HTTP ${status:-000}." >&2
    return 1
  }
  cat "${body}"
}

vault_body="$(probe_json vault "${vault_endpoint}/v1/sys/health?standbyok=true&perfstandbyok=true")" || exit 1
jq -e '(.initialized == true) and (.sealed == false)' <<<"${vault_body}" >/dev/null || {
  echo '::error::Shared Vault is not initialized and unsealed.' >&2
  exit 1
}

observability_body="$(probe_json observability "${observability_endpoint}/grafana/api/health")" || exit 1
jq -e '.database == "ok"' <<<"${observability_body}" >/dev/null || {
  echo '::error::Shared Observability Grafana database is not ready.' >&2
  exit 1
}

iam_body="$(probe_json iam "${iam_endpoint}/.well-known/openid-configuration")" || exit 1
jq -e --arg issuer "${expected_iam_issuer}" '.issuer == $issuer' <<<"${iam_body}" >/dev/null || {
  echo "::error::Shared IAM discovery issuer does not match ${expected_iam_issuer}." >&2
  exit 1
}

echo 'Shared readiness passed: Vault → Observability → IAM (read-only).'
