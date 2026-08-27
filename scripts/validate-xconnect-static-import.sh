#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module_root="${repo_root}/tools/xconnect-gateway-agent"
fixture_root="${repo_root}/tests/fixtures/xconnect-static-import"
temp_dir="$(mktemp -d /tmp/xconnect-static-import.XXXXXX)"
trap 'rm -rf "${temp_dir}"' EXIT
tool="${temp_dir}/xconnect-static-import"
go -C "${module_root}" build -trimpath -o "${tool}" ./cmd/xconnect-static-import

"${tool}" import \
  --input "${fixture_root}/group-vars.yml" \
  --network-id network-private \
  --owner-user-id 11111111-1111-4111-8111-111111111111 \
  >"${temp_dir}/import.json" 2>"${temp_dir}/import.stderr"
cmp "${fixture_root}/import.golden.json" "${temp_dir}/import.json"
rg -q '^dry-run: no Controller request sent$' "${temp_dir}/import.stderr"

"${tool}" diff \
  --input "${fixture_root}/group-vars.yml" \
  --snapshot "${fixture_root}/gateway-snapshot.json" \
  --attachment gateway-a \
  >"${temp_dir}/diff.json"
cmp "${fixture_root}/diff-equal.golden.json" "${temp_dir}/diff.json"

set +e
"${tool}" diff \
  --input "${fixture_root}/group-vars.yml" \
  --snapshot "${fixture_root}/gateway-snapshot.json" \
  --attachment gateway-b \
  >"${temp_dir}/drift.json"
drift_status=$?
"${tool}" import \
  --input "${fixture_root}/group-vars-secret.yml" \
  --network-id network-private \
  --owner-user-id 11111111-1111-4111-8111-111111111111 \
  >"${temp_dir}/secret.json" 2>"${temp_dir}/secret.stderr"
secret_status=$?
set -e

if [[ "${drift_status}" -ne 3 ]]; then
  echo "static drift did not return CI exit code 3" >&2
  exit 1
fi
if [[ "${secret_status}" -ne 2 ]]; then
  echo "secret-bearing client input was not rejected with exit code 2" >&2
  exit 1
fi

python3 -m json.tool "${temp_dir}/drift.json" >/dev/null
rg -q '"status": "drift"' "${temp_dir}/drift.json"

"${tool}" import \
  --input "${repo_root}/group_vars/xworkmate_bridge_distributed.yml" \
  --network-id legacy-private \
  --owner-user-id 11111111-1111-4111-8111-111111111111 \
  >"${temp_dir}/current-baseline.json" 2>/dev/null
python3 -m json.tool "${temp_dir}/current-baseline.json" >/dev/null
if rg -n --ignore-case 'private_key|auth_id|password|token|transport_credential' "${temp_dir}/current-baseline.json"; then
  echo "generated import document contains a prohibited credential field" >&2
  exit 1
fi

if rg -n 'wg (set|syncconf)|nft (add|delete|flush|insert|replace)|ansible-playbook' \
  "${module_root}/cmd/xconnect-static-import" \
  "${module_root}/internal/staticmigration" --glob '*.go' --glob '!**/*_test.go'; then
  echo "static migration tool contains a runtime or Ansible write path" >&2
  exit 1
fi

unformatted="$(gofmt -l "${module_root}/cmd/xconnect-static-import" "${module_root}/internal/staticmigration")"
if [[ -n "${unformatted}" ]]; then
  echo "unformatted static migration Go files: ${unformatted}" >&2
  exit 1
fi

echo "xconnect static import and shadow diff checks passed"
