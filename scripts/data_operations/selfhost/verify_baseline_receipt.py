#!/usr/bin/env python3
"""Fail closed unless a child run produced this host's real baseline receipt."""

import json
import os
import re
import sys
from pathlib import Path


path = Path(os.environ["BASELINE_RECEIPT"])
try:
    receipt = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"baseline receipt missing or invalid: {exc}") from exc
expected = {
    "schema": 1,
    "parent_workflow_run_id": os.environ["EXPECTED_PARENT_RUN_ID"],
    "baseline_workflow_run_id": os.environ["EXPECTED_BASELINE_RUN_ID"],
    "target_host": os.environ["EXPECTED_HOST"],
    "acceptance_run_id": os.environ["EXPECTED_ACCEPTANCE_RUN_ID"],
}
for field, value in expected.items():
    if receipt.get(field) != value:
        raise SystemExit(f"baseline receipt {field} does not match the requested run")
if receipt.get("captured_state") not in {"present", "absent"}:
    raise SystemExit("baseline receipt does not describe a captured database state")
counts = receipt.get("row_counts")
if not isinstance(counts, dict) or any(
    not isinstance(value, int) or value < 0 for value in counts.values()
):
    raise SystemExit("baseline receipt has invalid row counts")
if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", str(receipt.get("correlation_id", ""))):
    raise SystemExit("baseline receipt correlation id is invalid")
print("Accepted cross-run baseline receipt for this parent run and target host.")
