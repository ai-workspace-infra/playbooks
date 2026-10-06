#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
script="${repo_root}/scripts/data_operations/selfhost/acceptance.sh"
guard="${repo_root}/scripts/data_operations/selfhost/init_guard.sh"
workflow="${repo_root}/.github/workflows/selfhost-database-operations.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
export SSH_LOG="$tmp/ssh.log"
printf '{"environment":"uat","web-saas-uat":{"ip":"192.0.2.10","ansible_user":"ubuntu","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"
cat >"$tmp/bin/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$SSH_LOG"
cat >/dev/null
if [[ "${!#}" == *"baseline"* ]]; then
  printf 'baseline=captured\nstate=present\nmigration=10:false\nrows_users=1\nrows_identities=0\nrows_subscriptions=1\n'
fi
SH
chmod +x "$tmp/bin/ssh"
export PATH="$tmp/bin:$PATH" CMDB_FILE="$tmp/cmdb.json" REQUESTED_ENVIRONMENT=uat CONFIG_JSON='{"target_host":"web-saas-uat","acceptance_run_id":"123"}'
pass=0
check() { local name="$1"; shift; if "$@"; then ((pass+=1)); echo "[PASS] $name"; else echo "[FAIL] $name" >&2; exit 1; fi; }

echo '=== Selfhost identity and executor contract ==='
if REQUESTED_ENVIRONMENT=uat bash "$script" baseline >/dev/null 2>&1; then :; fi
check 'valid non-root CMDB identity uses sudo -n and its validated user' grep -Eq 'ubuntu@192\.0\.2\.10 sudo -n bash -s -- baseline' "$SSH_LOG"

before="$(wc -l <"$SSH_LOG")"
if CONFIG_JSON='{"target_host":"192.0.2.10","acceptance_run_id":"123"}' bash "$script" baseline >/dev/null 2>&1; then exit 1; fi
check 'arbitrary IP target is rejected before SSH' test "$(wc -l <"$SSH_LOG")" = "$before"

if printf '{"environment":"prod","web-saas-uat":{"ip":"192.0.2.10","ansible_user":"ubuntu","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"; then :; fi
if bash "$guard" web-saas-uat >/dev/null 2>&1; then exit 1; fi
check 'CMDB environment mismatch is rejected before SSH' test "$(wc -l <"$SSH_LOG")" = "$before"

printf '{"environment":"uat","web-saas-uat":{"ip":"192.0.2.10","ansible_user":"ubuntu;touch /tmp/pwned","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"
if bash "$guard" web-saas-uat >/dev/null 2>&1; then exit 1; fi
check 'shell-injected CMDB username is rejected before SSH' test "$(wc -l <"$SSH_LOG")" = "$before"

printf '{"environment":"uat","web-saas-uat":{"ip":"192.0.2.10","ansible_user":"ubuntu","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"
bash "$guard" web-saas-uat >/dev/null
check 'init guard connects only after identity checks and uses sudo -n' grep -Eq 'ubuntu@192\.0\.2\.10 sudo -n bash -s' "$SSH_LOG"

python3 - "$workflow" <<'PY'
import sys, yaml
w = yaml.safe_load(open(sys.argv[1]))
j = w["jobs"]["execute"]
checkout = next(s for s in j["steps"] if s.get("name") == "Check out Playbooks execution owner")
assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
assert "${{ inputs.environment }}" in j["env"]["VAULT_ROLE"]
assert j["env"]["VAULT_ROLE"] != "github-actions-platform-ops-toolkit-env"
assert "legacy_import" not in open(sys.argv[1]).read()
init = next(s for s in j["steps"] if s.get("name") == "Initialize schema only when explicitly requested")
pause = next(s for s in j["steps"] if s.get("id") == "pause")
assert "init_guard.sh" in pause["run"] and pause["run"].index("init_guard.sh") < pause["run"].index("application-state.sh stop")
assert j["steps"].index(pause) < j["steps"].index(init)
assert "account-database-config.json" in init["run"]
resume = next(s for s in j["steps"] if s.get("name") == "Resume application services after explicit initialization")
assert "always()" in resume["if"] and "steps.pause.outcome == 'success'" in resume["if"]
assert next(s for s in j["steps"] if "immutable Accounts release tag for initialization" in s.get("name", ""))["if"] == "${{ inputs.operation == 'selfhost_init' }}"
print("[PASS] reusable workflow checkout, environment role, and guarded explicit init contract")
PY
((pass+=1))
echo "web_saas_upgrade_acceptance_test: $pass checks passed"
