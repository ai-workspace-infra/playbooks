#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
config_guard="${repo_root}/roles/uat_data_import/files/validate_config.py"
run_guard="${repo_root}/roles/uat_data_import/files/validate_caller_run.py"
cmdb_guard="${repo_root}/roles/uat_data_import/files/validate_cmdb.py"
test_dir="$(mktemp -d)"
trap 'rm -rf "${test_dir}"' EXIT

export REQUESTED_ENVIRONMENT=uat CORRELATION_ID=caller-cmdb-test
if CONFIG_JSON='{"confirm_legacy_import":true,"caller_run_id":"12x"}' python3 "${config_guard}" >/dev/null 2>&1; then
  echo 'Non-decimal caller_run_id was accepted.' >&2; exit 1
fi
if CONFIG_JSON='{"confirm_legacy_import":true,"caller_run_id":"123","accounts_target_backend":"vps","accounts_migration_mode":"metadata"}' python3 "${config_guard}" >/dev/null 2>&1; then
  echo 'Invalid import mode was accepted.' >&2; exit 1
fi
CONFIG_JSON='{"confirm_legacy_import":true,"caller_run_id":"123"}' python3 "${config_guard}" >/dev/null
output_file="${test_dir}/request.outputs"
GITHUB_OUTPUT="${output_file}" CONFIG_JSON='{"confirm_legacy_import":true,"caller_run_id":123}' python3 "${config_guard}" >/dev/null
grep -Fxq 'caller_run_id=123' "${output_file}"

valid_run='{"id":123,"repository":{"full_name":"example/service"},"head_repository":{"full_name":"example/service"},"path":".github/workflows/selfhost-orchestrator.yml","head_branch":"main","status":"completed","conclusion":"success"}'
printf '%s' "${valid_run}" | python3 "${run_guard}" --repository example/service --run-id 123 >/dev/null
for altered in \
  '{"id":124,"repository":{"full_name":"example/service"},"head_repository":{"full_name":"example/service"},"path":".github/workflows/selfhost-orchestrator.yml","head_branch":"main","status":"completed","conclusion":"success"}' \
  '{"id":123,"repository":{"full_name":"other/service"},"head_repository":{"full_name":"other/service"},"path":".github/workflows/selfhost-orchestrator.yml","head_branch":"main","status":"completed","conclusion":"success"}' \
  '{"id":123,"repository":{"full_name":"example/service"},"head_repository":{"full_name":"example/service"},"path":".github/workflows/other.yml","head_branch":"main","status":"completed","conclusion":"success"}' \
  '{"id":123,"repository":{"full_name":"example/service"},"head_repository":{"full_name":"example/service"},"path":".github/workflows/selfhost-orchestrator.yml","head_branch":"feature/test","status":"completed","conclusion":"success"}' \
  '{"id":123,"repository":{"full_name":"example/service"},"head_repository":{"full_name":"example/service"},"path":".github/workflows/selfhost-orchestrator.yml","head_branch":"main","status":"completed","conclusion":"failure"}'; do
  if printf '%s' "${altered}" | python3 "${run_guard}" --repository example/service --run-id 123 >/dev/null 2>&1; then
    echo 'Untrusted caller workflow run was accepted.' >&2; exit 1
  fi
done

mkdir -p "${test_dir}/cmdb"
printf '{"source.example":{"ip":"192.0.2.10"},"target.example":{"ip":"2001:db8::10"}}\n' >"${test_dir}/cmdb/cmdb.json"
(cd "${test_dir}" && CONFIG_JSON='{"accounts_source_host":"source.example","accounts_target_host":"target.example"}' python3 "${cmdb_guard}" >/dev/null)
if (cd "${test_dir}" && CONFIG_JSON='{"accounts_target_host":"missing.example"}' python3 "${cmdb_guard}" >/dev/null 2>&1); then
  echo 'CMDB missing configured target was accepted.' >&2; exit 1
fi

echo 'Caller CMDB guard tests passed.'
