#!/usr/bin/env python3
"""Write a sanitized receipt bound to the current caller run and attempt."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    environment = os.environ.get("OPERATION_ENVIRONMENT", "")
    operation = os.environ.get("OPERATION_NAME", "")
    correlation = os.environ.get("CORRELATION_ID", "")
    result = os.environ.get("OPERATION_RESULT", "")
    repository = os.environ.get("CALLER_REPOSITORY", "")
    caller_sha = os.environ.get("CALLER_SHA", "")
    workflow_ref = os.environ.get("CALLER_WORKFLOW_REF", "")
    run_id = os.environ.get("CALLER_RUN_ID", "")
    run_attempt = os.environ.get("CALLER_RUN_ATTEMPT", "")
    release_tag = os.environ.get("RELEASE_TAG", "")
    action_ref = os.environ.get("OWNER_ACTION_REF", "")

    require(environment in {"uat", "prod"}, "receipt environment must be uat or prod")
    require(operation in {"checkpoint", "probe", "baseline", "migrate"}, "receipt operation is unsupported")
    require(result in {"success", "failure", "cancelled"}, "receipt result is invalid")
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", correlation)),
            "receipt correlation_id is invalid")
    require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)),
            "receipt caller repository is invalid")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", caller_sha)), "receipt caller SHA must be a full commit SHA")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", action_ref)), "receipt owner SHA must be a full commit SHA")
    require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), "receipt caller run ID is invalid")
    require(bool(re.fullmatch(r"[1-9][0-9]*", run_attempt)), "receipt caller run attempt is invalid")
    require(workflow_ref.startswith(repository + "/.github/workflows/"),
            "receipt workflow_ref must belong to the caller repository")
    require(operation not in {"baseline", "migrate"} or environment == "uat",
            "baseline and migrate receipts are UAT-only")

    payload = {
        "schema": "serverless-database-operation-receipt/v1",
        "owner": "ai-workspace-infra/playbooks",
        "owner_action_ref": action_ref,
        "caller": {
            "repository": repository,
            "sha": caller_sha,
            "workflow_ref": workflow_ref,
            "run_id": int(run_id),
            "run_attempt": int(run_attempt),
        },
        "environment": environment,
        "operation": operation,
        "correlation_id": correlation,
        "release_tag": release_tag,
        "result": result,
    }
    path = Path(os.environ["RECEIPT_PATH"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_name = f"serverless-database-{run_id}-{run_attempt}-{correlation}"
    output = os.environ.get("GITHUB_OUTPUT")
    require(output, "GITHUB_OUTPUT is required")
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(f"data_run_id={run_id}\n")
        stream.write(f"data_run_attempt={run_attempt}\n")
        stream.write(f"receipt_artifact_name={artifact_name}\n")
        stream.write(f"receipt_sha256={digest}\n")
        stream.write(f"operation_result={result}\n")
    print(f"Wrote sanitized {result} receipt for {operation}/{environment}, run {run_id} attempt {run_attempt}.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(2)
