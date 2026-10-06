#!/usr/bin/env python3
"""Explicit, additive repair of canonical UAT identity trigger dependencies.

No table replacement, source writes, arbitrary SQL, or secret-bearing output.
The operator must authorize the repair and name a successful Selfhost caller.
"""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


SQL = r"""
BEGIN;
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='60s';
LOCK TABLE public.users,public.sessions IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE repair_counts AS SELECT
 (SELECT count(*) FROM public.users) users,
 (SELECT count(*) FROM public.sessions) sessions;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
  WHERE t.tgrelid='public.users'::regclass AND t.tgname='trg_users_bump_version'
  AND p.proname='bump_version' AND t.tgenabled='O') OR
 NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
  WHERE t.tgrelid='public.sessions'::regclass AND t.tgname='trg_sessions_bump_version'
  AND p.proname='bump_version' AND t.tgenabled='O') OR
 NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
  WHERE t.tgrelid='public.sessions'::regclass AND t.tgname='trg_sessions_set_updated_at'
  AND p.proname='set_updated_at' AND t.tgenabled='O') THEN
  RAISE EXCEPTION 'Canonical trigger contract is not present';
 END IF;
END $$;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM (VALUES ('users','version','bigint'),
 ('sessions','version','bigint'),('sessions','updated_at','timestamp with time zone'))
 AS expected(tab,col,typ) LEFT JOIN information_schema.columns c
 ON c.table_schema='public' AND c.table_name=expected.tab AND c.column_name=expected.col
 WHERE c.data_type IS DISTINCT FROM expected.typ OR c.is_nullable IS DISTINCT FROM 'NO') THEN
  RAISE EXCEPTION 'Trigger dependency types do not match canonical schema';
 END IF;
END $$;
-- Exercise the actual attached triggers, then undo every application row update.
SAVEPOINT trigger_probe;
UPDATE public.users SET updated_at=updated_at WHERE uuid=(SELECT uuid FROM public.users LIMIT 1);
UPDATE public.sessions SET expires_at=expires_at WHERE token=(SELECT token FROM public.sessions LIMIT 1);
ROLLBACK TO SAVEPOINT trigger_probe;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM repair_counts WHERE users<>(SELECT count(*) FROM public.users)
 OR sessions<>(SELECT count(*) FROM public.sessions)) THEN
  RAISE EXCEPTION 'Identity row counts changed during repair';
 END IF;
END $$;
COMMIT;
SELECT 'canonical-trigger-dependencies-verified';
"""


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError("Owner operation failed; private runtime output withheld")
    return result.stdout


def main():
    if os.environ.get("CONFIRM_UAT_IDENTITY_TRIGGER_REPAIR") != "true":
        raise RuntimeError("Explicit UAT identity trigger repair confirmation required")
    caller = os.environ.get("CALLER_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", caller):
        raise RuntimeError("A successful Selfhost caller run is required")
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="uat-trigger-repair-") as tmp:
        work = Path(tmp)
        run(["gh", "run", "download", caller, "--repo", "ai-workspace-infra/platform-ops-toolkit",
             "--name", "platform-ops-toolkit-cmdb", "--dir", tmp])
        token = run(["gh", "auth", "token"]).strip()
        env = dict(os.environ, REQUESTED_ENVIRONMENT="uat", CALLER_RUN_ID=caller,
                   CONFIG_JSON=json.dumps({"target_host": "web-saas-uat"}),
                   GH_TOKEN=token, CMDB_FILE=str(work / "cmdb.json"))
        run(["python3", str(root / "verify_caller_cmdb.py")], env=env)
        meta = json.loads(run(["gh", "api", "repos/ai-workspace-infra/platform-ops-toolkit/actions/runs/" + caller]))
        if meta.get("conclusion") != "success":
            raise RuntimeError("Repair requires a completed successful caller")
        cmdb = json.loads((work / "cmdb.json").read_text())
        host = cmdb["web-saas-uat"]
        user = host.get("ansible_user", "root")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise RuntimeError("Invalid target SSH identity")
        vault = json.loads(run(["vault", "kv", "get", "-format=json", "kv/CICD/uat"]))["data"]["data"]
        key = work / "key"
        key.write_bytes(base64.b64decode(vault["SSH_PRIVATE_DEPLOY_KEY_B64"], validate=True))
        key.chmod(0o600)
        # A full pre-repair dump stays on the UAT host, root-only. Never upload it.
        remote = """set -euo pipefail
umask 077
install -d -m 700 /var/backups/platform-ops
backup=$(mktemp /var/backups/platform-ops/uat-identity-before-trigger-repair.XXXXXX.dump)
docker exec web-saas-postgresql pg_dump -U account_user -d account -Fc > "$backup"
test -s "$backup"
chmod 600 "$backup"
docker exec -i web-saas-postgresql psql -XAtq -v ON_ERROR_STOP=1 -U account_user -d account <<'REPAIR_SQL'
""" + SQL + "\nREPAIR_SQL\n"
        command = (["sudo", "-n"] if user != "root" else []) + ["bash", "-s"]
        output = run(["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                      "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
                      user + "@" + host["ip"], shlex.join(command)], input=remote)
        if "canonical-trigger-dependencies-verified" not in output.splitlines():
            raise RuntimeError("Repair did not return verified completion")
        receipt = {"schema": "uat-identity-trigger-repair/v1", "environment": "uat",
                   "target_host": "web-saas-uat", "caller_run_id": caller,
                   "dependencies": ["users.version", "sessions.version", "sessions.updated_at"],
                   "backup": "host-local-root-only", "application_row_counts_unchanged": True,
                   "trigger_probe_rolled_back": True, "success": True}
        print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise SystemExit("UAT trigger repair failed; private runtime output withheld")
