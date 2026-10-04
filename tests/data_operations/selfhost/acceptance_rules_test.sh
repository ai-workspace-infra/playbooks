#!/usr/bin/env bash
set -euo pipefail

# Contract-level companion to web_saas_upgrade_acceptance_postgres_test.sh.
# The latter executes the actual remote acceptance program against PostgreSQL
# 17; these checks pin its key rejection rules and prove invalid requests never
# reach SSH.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
script="$root/scripts/data_operations/selfhost/acceptance.sh"
workflow="$root/.github/workflows/selfhost-database-operations.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat >"$tmp/bin/ssh" <<'SH'
#!/usr/bin/env bash
echo called >>"$SSH_LOG"
cat >/dev/null
SH
chmod +x "$tmp/bin/ssh"
printf '{"environment":"uat","web-saas-uat":{"ip":"127.0.0.1","ansible_user":"root","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"
export PATH="$tmp/bin:$PATH" SSH_LOG="$tmp/ssh.log" CMDB_FILE="$tmp/cmdb.json" REQUESTED_ENVIRONMENT=uat
export CONFIG_JSON='{"target_host":"web-saas-uat","acceptance_run_id":"rule-test"}'
pass=0
check() { local label="$1"; shift; if "$@"; then ((pass+=1)); echo "[PASS] $label"; else echo "[FAIL] $label" >&2; exit 1; fi; }

if RELEASE_TAG= bash "$script" verify >/dev/null 2>&1; then exit 1; fi
check 'invalid/missing immutable release tag blocks before SSH' test ! -e "$SSH_LOG"
if CONFIG_JSON='{"target_host":"web-saas-uat","acceptance_run_id":"bad run"}' bash "$script" baseline >/dev/null 2>&1; then exit 1; fi
check 'invalid acceptance correlation blocks before SSH' test ! -e "$SSH_LOG"

python3 - "$script" "$workflow" <<'PY'
import sys
from pathlib import Path
source = Path(sys.argv[1]).read_text()
workflow = Path(sys.argv[2]).read_text()
checks = {
    "Accounts and Console running image tags must match release_tag": '[[ "$image" == *":${release_tag}" ]]',
    "Accounts readiness and ping plus Console HTTP status are exercised": 'http_status "$accounts" 8080 /readyz' in source and 'http_status "$accounts" 8080 /api/ping' in source and 'http_status "$console" 3000 /' in source,
    "HTTP acceptance rejects non-2xx/3xx status": '[[ "$status" =~ ^[23][0-9][0-9]$ ]]' in source,
    "schema migration dirty state is rejected": '[[ "$migration" != *:true ]]' in source,
    "an exact expected schema version is enforced": '"${expected_version}:false"' in source,
    "user, identity, and subscription fingerprints are compared": all(f'for table in users identities subscriptions' in source for _ in [0]),
    "volatile timestamps/session fields are excluded from hashes": all(x in source for x in ("last_active_at", "session_token", "last_seen_at", "last_checked_at")),
    "schema init is explicitly guarded before Ansible writes": 'init_guard.sh' in workflow and workflow.index('init_guard.sh') < workflow.index('ansible-playbook'),
}
for label, ok in checks.items():
    if not ok:
        raise SystemExit("[FAIL] " + label)
    print("[PASS] " + label)

# Compile and execute the embedded init tag check with fake git output. This
# catches malformed ^{commit} quoting before a runner reaches the workflow.
import os, subprocess, tempfile, yaml
from unittest.mock import patch
doc = yaml.load(workflow, Loader=yaml.BaseLoader)
step = next(s for s in doc["jobs"]["execute"]["steps"] if s.get("name") == "Require immutable Accounts release tag for initialization")
block = step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
compile(block, "workflow-init-tag-check", "exec")
calls = []
def fake_git(args, **kwargs):
    calls.append(args)
    return "abc123\n"
with tempfile.NamedTemporaryFile() as env_file, patch.dict(os.environ, {"REQUESTED_ACCOUNTS_REF": "uat-daily-build-2026.10.04-r1", "GITHUB_ENV": env_file.name}), patch.object(subprocess, "check_output", side_effect=fake_git):
    exec(compile(block, "workflow-init-tag-check", "exec"), {})
assert calls[0][-1] == "refs/tags/uat-daily-build-2026.10.04-r1^{commit}"
assert calls[1][-1] == "HEAD"
print("[PASS] selfhost_init embedded Accounts tag expression resolves ^{commit} with fake git")
PY
((pass+=1))
echo "acceptance_rules_test: $pass request checks passed (behavioral cases run in the PostgreSQL 17 companion)"
