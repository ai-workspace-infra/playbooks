#!/usr/bin/env bash
set -euo pipefail

pipeline_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

# ansible-playbook must never be reached when the target host is empty: an empty
# --limit matches zero hosts and the run would exit 0 without deploying anything.
mkdir -p "${workdir}/bin"
cat >"${workdir}/bin/ansible-playbook" <<'STUB'
#!/usr/bin/env bash
echo "ansible-playbook must not run" >&2
exit 99
STUB
chmod +x "${workdir}/bin/ansible-playbook"

expect_guard() {
  local script="$1"; shift
  local rc=0 out
  out="$(env -i PATH="${workdir}/bin:${PATH}" HOME="${workdir}" "$@" bash "${pipeline_dir}/${script}" 2>&1)" || rc=$?
  [[ "${rc}" -eq 1 ]] || { echo "${script}: expected exit 1, got ${rc}" >&2; echo "${out}" >&2; exit 1; }
  grep -Fq 'missing or empty required environment variable' <<<"${out}" || { echo "${script}: guard message missing" >&2; echo "${out}" >&2; exit 1; }
}

expect_guard deploy-monitor-agent.sh
expect_guard deploy-monitor-agent.sh MATRIX_HOST=
expect_guard deploy-action-runner.sh
expect_guard deploy-action-runner.sh MATRIX_HOST=host1 VAULT_ENV_PATH=uat

echo "require_env_guard_test: PASS"
