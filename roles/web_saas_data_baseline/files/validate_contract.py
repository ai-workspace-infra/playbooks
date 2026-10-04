#!/usr/bin/env python3
"""Validate UAT baseline evidence metadata; never connects to a database."""

import json
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_baseline(payload):
    manifest = payload.get("manifest")
    require(payload.get("environment") == "uat", "environment must be uat")
    require(isinstance(manifest, dict), "manifest must be an object")
    required = (
        "source_db_id",
        "target_db_id",
        "snapshot_id",
        "config_revision",
        "sanitization_policy",
        "baseline_id",
    )
    for key in required:
        require(nonempty(manifest.get(key)), f"{key} is required")
    require(manifest["source_db_id"] != manifest["target_db_id"], "source and target database identities must differ")
    require(manifest.get("source_environment") == "uat", "source_environment must be uat; PROD source data is forbidden")
    allowlist = manifest.get("approved_allowlist")
    require(isinstance(allowlist, list) and allowlist and all(nonempty(item) for item in allowlist), "approved_allowlist must be a nonempty list")
    approval = manifest.get("approval")
    require(isinstance(approval, dict), "explicit approval evidence is required")
    for key in ("approval_id", "approver", "approved_at", "scope"):
        require(nonempty(approval.get(key)), f"approval.{key} is required")
    require(approval.get("status") == "approved", "approval.status must be approved")
    for key in ("source_db_id", "target_db_id", "snapshot_id", "baseline_id"):
        require(approval.get(key) == manifest[key], f"approval.{key} must bind this manifest")
    for key in ("users_sample_count", "subscriptions_sample_count"):
        value = manifest.get(key)
        require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"{key} must be a positive integer")
    return {
        "schema": 1,
        "phase": "uat_data_baseline_manifest",
        "status": "manifest_validated",
        "environment": "uat",
        "source_environment": manifest["source_environment"],
        "source_db_id": manifest["source_db_id"],
        "target_db_id": manifest["target_db_id"],
        "snapshot_id": manifest["snapshot_id"],
        "config_revision": manifest["config_revision"],
        "approved_allowlist": allowlist,
        "sanitization_policy": manifest["sanitization_policy"],
        "baseline_id": manifest["baseline_id"],
        "users_sample_count": manifest["users_sample_count"],
        "subscriptions_sample_count": manifest["subscriptions_sample_count"],
        "approval": approval,
        "data_copied": False,
        "database_initialized": False,
        "seeded": False,
    }


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "baseline":
        raise ValueError("usage: validate_contract.py baseline")
    payload = json.load(sys.stdin)
    result = validate_baseline(payload)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
