#!/usr/bin/env python3
"""Bind the downloaded flat GCP CMDB to an approved caller workflow run."""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


environment = os.environ["REQUESTED_ENVIRONMENT"]
config = json.loads(os.environ["CONFIG_JSON"])
host = config.get("target_host")
run_id = os.environ["CALLER_RUN_ID"]
if environment not in {"uat", "prod", "sit"} or host != f"web-saas-{environment}":
    raise SystemExit("caller/CMDB identity check requires the canonical environment host")
if not re.fullmatch(r"[1-9][0-9]*", run_id):
    raise SystemExit("caller_run_id is invalid")

api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
token = os.environ["GH_TOKEN"]


def get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise SystemExit("GitHub caller metadata response is invalid")
    return result


try:
    run = get_json(
        f"{api}/repos/ai-workspace-infra/platform-ops-toolkit/actions/runs/{run_id}"
    )
    workflow_id = run.get("workflow_id")
    if (
        run.get("repository", {}).get("full_name") != "ai-workspace-infra/platform-ops-toolkit"
        or not isinstance(workflow_id, int)
        or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", "")))
    ):
        raise SystemExit("caller run is not a valid platform-ops-toolkit workflow run")
    workflow = get_json(
        f"{api}/repos/ai-workspace-infra/platform-ops-toolkit/actions/workflows/{workflow_id}"
    )
except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    raise SystemExit(f"cannot verify trusted caller workflow metadata: {type(exc).__name__}") from exc
if workflow.get("path") != ".github/workflows/selfhost-orchestrator.yml":
    raise SystemExit("caller run is not from the approved selfhost orchestrator workflow")
branch = str(run.get("head_branch", ""))
if run.get("event") != "workflow_dispatch" or not (
    branch == "main" or re.fullmatch(r"(?:uat-)?daily-build-\d{4}\.\d{2}\.\d{2}(?:-r[1-9]\d*)?|v\d[0-9.r-]*", branch)
):
    raise SystemExit("caller CMDB must originate from a reviewed main/tag workflow_dispatch, not a PR or feature branch")
if run.get("status") == "completed":
    if run.get("conclusion") != "success":
        raise SystemExit("completed caller run must have succeeded")
elif run.get("status") != "in_progress":
    raise SystemExit("caller run must be the active approved deployment or a successful prior run")

try:
    cmdb = json.loads(Path(os.environ["CMDB_FILE"]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"caller CMDB artifact is missing or invalid: {exc}") from exc
if not isinstance(cmdb, dict) or cmdb.get("environment") != environment:
    raise SystemExit("CMDB top-level environment identity does not match the verified caller")
record = cmdb.get(host)
if not isinstance(record, dict):
    raise SystemExit("CMDB has no canonical Web SaaS host record for this environment")
if record.get("environment", environment) != environment:
    raise SystemExit("CMDB host record explicitly conflicts with the requested environment")
if "web_saas" not in (record.get("groups") or []):
    raise SystemExit("CMDB host record is not in the web_saas group")
if not re.fullmatch(r"[A-Fa-f0-9:.]+", str(record.get("ip", ""))):
    raise SystemExit("CMDB host record has no valid IP address")
print("Verified approved caller workflow and environment-bound GCP CMDB host record.")
