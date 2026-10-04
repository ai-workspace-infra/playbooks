#!/usr/bin/env python3
"""Write sanitized, parent-bound evidence from a real remote baseline capture."""

import json
import os
import re
import sys
from pathlib import Path


if len(sys.argv) != 3:
    raise SystemExit("usage: write_baseline_receipt.py OUTPUT_LOG RECEIPT_JSON")
config = json.loads(os.environ["CONFIG_JSON"])
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
values = {}
for line in lines:
    match = re.fullmatch(r"([a-z][a-z0-9_]*)=([A-Za-z0-9._:-]+)", line.strip())
    if match:
        values[match.group(1)] = match.group(2)
if values.get("baseline") not in {"captured", "already-captured"}:
    raise SystemExit("remote baseline did not report a completed capture")
if values.get("state") not in {"present", "absent"}:
    raise SystemExit("remote baseline receipt has no valid database state")
for key, value in values.items():
    if key.startswith("rows_") and not re.fullmatch(r"[0-9]+", value):
        raise SystemExit("baseline row count is invalid")

receipt = {
    "schema": 1,
    "parent_workflow_run_id": os.environ["GITHUB_PARENT_RUN_ID"],
    "baseline_workflow_run_id": os.environ["GITHUB_RUN_ID"],
    "correlation_id": os.environ["CORRELATION_ID"],
    "target_host": config["target_host"],
    "acceptance_run_id": config["acceptance_run_id"],
    "captured_state": values["state"],
    "migration_state": values.get("migration", "unknown"),
    "row_counts": {key[5:]: int(value) for key, value in values.items() if key.startswith("rows_")},
}
destination = Path(sys.argv[2])
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
destination.chmod(0o600)
