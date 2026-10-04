#!/usr/bin/env python3
"""Verify trusted checkpoint metadata and host-side isolated restore evidence."""

import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TRUSTED_WORKFLOW_PATH = ".github/workflows/environment-data-operations.yml"
MAX_RECEIPT_BYTES = 65536
MAX_ARTIFACT_BYTES = 131072


def fail(message):
    raise ValueError(message)


def api_json(url, token):
    request = Request(url, headers={"Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def locate_trusted_artifact(token, repository, run_id, artifact_id, artifact_name, caller_sha):
    api = f"https://api.github.com/repos/{repository}"
    run = api_json(f"{api}/actions/runs/{run_id}", token)
    run_path = str(run.get("path", "")).split("@", 1)[0]
    active_same_run = run.get("status") == "in_progress" and run_id == os.environ.get("CURRENT_RUN_ID")
    completed_successfully = run.get("status") == "completed" and run.get("conclusion") == "success"
    if (str(run.get("id")) != run_id
            or run.get("head_repository", {}).get("full_name") != repository
            or run_path != TRUSTED_WORKFLOW_PATH
            or run.get("head_branch") != "main"
            or run.get("head_sha") != caller_sha
            or not (active_same_run or completed_successfully)):
        fail("checkpoint_run_id is not an authorized main environment-data-operations.yml run at the pinned caller SHA")
    artifacts = api_json(f"{api}/actions/runs/{run_id}/artifacts?per_page=100", token).get("artifacts", [])
    matches = [item for item in artifacts if str(item.get("id")) == artifact_id
               and item.get("name") == artifact_name and not item.get("expired")]
    if len(matches) != 1:
        fail("backup_receipt artifact_id/name is not an available artifact from checkpoint_run_id")
    if int(matches[0].get("size_in_bytes", MAX_ARTIFACT_BYTES + 1)) > MAX_ARTIFACT_BYTES:
        fail("checkpoint receipt artifact exceeds the metadata-only size limit")


def main():
    preflight = len(sys.argv) > 1 and sys.argv[1] == "--preflight"
    artifact_root = None if preflight else Path(sys.argv[1]).resolve()
    token = os.environ.get("GH_TOKEN", "")
    trusted_repository = os.environ.get("TRUSTED_REPOSITORY", "")
    expected_environment = os.environ.get("VAULT_ENV_PATH", "")
    expected_release_tag = os.environ.get("RELEASE_TAG", "")
    caller_sha = os.environ.get("TRUSTED_CALLER_SHA", "")
    current_run_id = os.environ.get("CURRENT_RUN_ID", "")
    config = json.loads(os.environ["CONFIG_JSON"])
    locator = config["backup_receipt"]
    repository = locator["source_repository"]
    run_id = str(config["checkpoint_run_id"])
    artifact_id = str(locator["artifact_id"])
    artifact_name = locator["artifact_name"]

    if (not token or repository != trusted_repository
            or not re.fullmatch(r"[^/]+/[^/]+", trusted_repository)):
        fail("checkpoint receipt is not owned by the trusted caller repository")
    if str(locator["source_run_id"]) != run_id:
        fail("backup_receipt source_run_id does not match checkpoint_run_id")
    if not re.fullmatch(r"[0-9a-f]{40}", caller_sha):
        fail("pinned caller SHA is missing or invalid")
    if not re.fullmatch(r"[0-9]+", current_run_id):
        fail("current workflow run ID is missing or invalid")
    locate_trusted_artifact(token, repository, run_id, artifact_id, artifact_name, caller_sha)
    if preflight:
        print("Trusted checkpoint run and small sanitized receipt artifact are available.")
        return

    files = [path for path in artifact_root.rglob("*") if path.is_file()]
    if len(files) != 1 or files[0].name != "backup-receipt.json":
        fail("trusted artifact must contain only the sanitized backup-receipt.json metadata file")
    receipt_path = files[0].resolve()
    if not receipt_path.is_relative_to(artifact_root) or receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
        fail("backup receipt is outside the artifact or exceeds the metadata size limit")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    expected_digest = locator["backup_sha256"]
    required_text = ("host_id", "backup_path", "database_identity", "restore_database_identity",
                     "verified_at", "evidence_ref")
    expected_schema_version = (str(config.get("expected_schema_version", ""))
                               if os.environ.get("REQUESTED_OPERATION") == "migrate" else "absent")
    sample_counts = receipt.get("sample_counts", {})
    backup_prefix = f"/data/backups/web-saas/{expected_environment}/{expected_release_tag}/{run_id}/"
    valid_samples = (isinstance(sample_counts, dict)
                     and isinstance(sample_counts.get("users"), int) and sample_counts["users"] > 0
                     and isinstance(sample_counts.get("subscriptions"), int) and sample_counts["subscriptions"] > 0)
    if (receipt.get("schema") != "serverless-data-backup-receipt/v1"
            or receipt.get("source_repository") != repository
            or str(receipt.get("source_run_id")) != run_id
            or receipt.get("caller_sha") != caller_sha
            or receipt.get("environment") != expected_environment
            or receipt.get("release_tag") != expected_release_tag
            or receipt.get("backup_sha256") != expected_digest
            or receipt.get("encrypted") is not True
            or receipt.get("restore_verified") is not True
            or receipt.get("host_environment") != expected_environment
            or receipt.get("restore_host_id") != receipt.get("host_id")
            or receipt.get("schema_version") != expected_schema_version
            or receipt.get("schema_dirty") is not False
            or receipt.get("database_identity") == receipt.get("restore_database_identity")
            or not valid_samples
            or not all(isinstance(receipt.get(key), str) and receipt[key].strip() for key in required_text)
            or not receipt["backup_path"].startswith(backup_prefix)
            or ".." in Path(receipt["backup_path"]).parts
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("backup_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)):
        fail("trusted receipt does not prove the encrypted /data backup, isolated database restore, nonempty samples, and clean expected version for this source run")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("checkpoint_verified=true\n")
    print("Verified sanitized trusted-run receipt, pinned main caller SHA, same-environment /data backup, isolated database restore, samples, and clean schema version.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError, OSError, HTTPError, URLError, json.JSONDecodeError) as error:
        print(f"::error::checkpoint evidence rejected: {error}", file=sys.stderr)
        raise SystemExit(2)
