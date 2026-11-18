#!/usr/bin/env python3
"""Validate that a CMDB artifact comes from the reviewed in-repository caller."""

import argparse
import json
import re
import sys


parser = argparse.ArgumentParser()
parser.add_argument("--repository", required=True)
parser.add_argument("--run-id", required=True)
args = parser.parse_args()

if not re.fullmatch(r"[0-9]{1,20}", args.run_id):
    raise SystemExit("caller run ID must contain 1-20 decimal digits")
try:
    run = json.load(sys.stdin)
except json.JSONDecodeError as exc:
    raise SystemExit(f"GitHub returned invalid run metadata: {exc.msg}")

checks = {
    "run ID": str(run.get("id", "")) == args.run_id,
    "repository": (run.get("repository") or {}).get("full_name") == args.repository,
    "head repository": (run.get("head_repository") or {}).get("full_name") == args.repository,
    "workflow path": run.get("path") == ".github/workflows/selfhost-orchestrator.yml",
    "reviewed ref": run.get("head_branch") == "main",
    "completed successfully": run.get("status") == "completed" and run.get("conclusion") == "success",
}
failed = [label for label, passed in checks.items() if not passed]
if failed:
    raise SystemExit("caller run is not trusted for CMDB reuse: " + ", ".join(failed))
print("Caller run validated: same repository, selfhost-orchestrator.yml on main, successful.")
