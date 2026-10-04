#!/usr/bin/env python3
"""Validate immutable inputs for the not-yet-supported Selfhost migrator."""

import json
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate(payload):
    request = payload.get("request")
    baseline = payload.get("baseline_manifest")
    backup = payload.get("backup_evidence")
    restore = payload.get("restore_evidence")
    require(payload.get("environment") == "uat", "role environment must be uat")
    require(isinstance(request, dict), "migration request must be an object")
    require(request.get("environment") == "uat", "migration is UAT-only")
    require(nonempty(request.get("target_db_id")), "target_db_id is required")
    require(nonempty(request.get("baseline_id")), "baseline_id is required")
    require(request.get("container") == "canonicalAccount", "target container must be canonicalAccount")
    candidate = request.get("candidate_sha256")
    accounts_revision = request.get("accounts_source_revision")
    require(isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate), "candidate_sha256 must be lowercase SHA-256")
    require(isinstance(accounts_revision, str) and re.fullmatch(r"[0-9a-f]{40,64}", accounts_revision), "accounts_source_revision must be an immutable Git commit")

    require(isinstance(baseline, dict), "validated baseline manifest metadata is required")
    require(baseline.get("status") == "manifest_validated", "baseline status must be manifest_validated")
    require(baseline.get("environment") == "uat", "baseline manifest must be UAT")
    require(baseline.get("baseline_id") == request["baseline_id"], "baseline_id does not match the migration request")
    require(baseline.get("target_db_id") == request["target_db_id"], "migration target must match the frozen baseline target")
    require(nonempty(baseline.get("source_db_id")), "baseline source provenance is required")
    require(baseline.get("source_environment") == "uat", "baseline source provenance must be UAT")
    require(nonempty(baseline.get("snapshot_id")), "baseline snapshot provenance is required")
    require(nonempty(baseline.get("config_revision")), "baseline config revision is required")
    require(isinstance(baseline.get("approved_allowlist"), list) and baseline["approved_allowlist"], "baseline allowlist metadata is missing")
    require(nonempty(baseline.get("sanitization_policy")), "baseline sanitization policy metadata is missing")
    baseline_approval = baseline.get("approval")
    require(isinstance(baseline_approval, dict) and baseline_approval.get("status") == "approved", "baseline approval metadata is missing or unapproved")
    for key in ("approval_id", "approver", "approved_at", "scope"):
        require(nonempty(baseline_approval.get(key)), f"baseline approval.{key} is required")
    for key in ("source_db_id", "target_db_id", "snapshot_id", "baseline_id"):
        require(baseline_approval.get(key) == baseline.get(key), f"baseline approval.{key} does not bind manifest")
    for key in ("users_sample_count", "subscriptions_sample_count"):
        value = baseline.get(key)
        require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"baseline {key} must be a positive integer")
    expected = request.get("expected_version")
    target = request.get("target_version")
    observed = request.get("observed_version")
    require(isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0, "expected_version must be a nonnegative integer")
    require(isinstance(target, int) and not isinstance(target, bool) and target > expected, "target_version must be greater than expected_version")
    require(isinstance(observed, int) and not isinstance(observed, bool) and observed == expected, "observed_version must equal expected_version")
    require(request.get("observed_dirty") is False, "dirty migration state is forbidden and must never be force-cleared")
    require(isinstance(request.get("migration_checksum"), str) and re.fullmatch(r"[0-9a-f]{64}", request["migration_checksum"]), "migration_checksum must be lowercase SHA-256")
    for key in ("lock_timeout_seconds", "statement_timeout_seconds"):
        value = request.get(key)
        require(isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 3600, f"{key} must be between 1 and 3600")
    require(request.get("advisory_lock") is True, "advisory_lock must be required")
    require(request.get("single_migration_only") is True, "exactly one reviewed migration must be selected")
    approval = request.get("approval")
    require(isinstance(approval, dict) and approval.get("status") == "approved", "explicit migration approval is required")
    for key in ("approval_id", "approver", "approved_at", "scope"):
        require(isinstance(approval.get(key), str) and approval[key].strip(), f"approval.{key} is required")
    for key, expected_value in (
        ("environment", "uat"),
        ("target_db_id", request["target_db_id"]),
        ("baseline_id", request["baseline_id"]),
        ("candidate_sha256", candidate),
        ("accounts_source_revision", accounts_revision),
    ):
        require(approval.get(key) == expected_value, f"approval.{key} must bind this migration request")

    require(isinstance(backup, dict), "backup component evidence is required")
    backup_requirements = {
        "phase": "database_backup_component",
        "status": "passed",
        "database": "account",
        "environment": "uat",
        "source_database_id": request["target_db_id"],
        "baseline_id": request["baseline_id"],
        "encrypted": True,
        "durable": True,
    }
    for key, expected_value in backup_requirements.items():
        require(backup.get(key) == expected_value, f"backup evidence {key} does not match required evidence")
    for key in (
        "checkpoint_id",
        "archive_path",
        "archive_sha256",
        "schema_sha256",
        "data_sha256",
        "database_system_identifier",
    ):
        require(nonempty(backup.get(key)), f"backup evidence {key} is required")
    # data_sha256 covers full table rows AND all public sequence states.
    for key in ("archive_sha256", "schema_sha256", "data_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", backup[key]) is not None, f"backup {key} must be lowercase SHA-256")
    require(backup.get("schema_version") == expected, "backup schema_version must equal expected_version")
    require(isinstance(backup.get("existing_users"), int) and backup["existing_users"] > 0, "backup user sample must be nonempty")
    require(isinstance(backup.get("subscriptions"), int) and backup["subscriptions"] > 0, "backup subscription sample must be nonempty")
    require(nonempty(backup.get("authorized_subscription_sample_id")), "authorized subscription sample reference is required")

    require(isinstance(restore, dict), "independent isolated-restore evidence is required")
    restore_requirements = {
        "phase": "database_restore_verify_component",
        "status": "passed",
        "database": "account",
        "environment": "uat",
        "source_database_id": request["target_db_id"],
        "baseline_id": request["baseline_id"],
        "schema_version": expected,
        "archive_sha256": backup["archive_sha256"],
        "schema_sha256": backup["schema_sha256"],
        "data_sha256": backup["data_sha256"],
        "database_system_identifier": backup["database_system_identifier"],
        "existing_users": backup["existing_users"],
        "subscriptions": backup["subscriptions"],
        "isolated_restore_verified": True,
        "source_restore_schema_matches": True,
        "restored_data_matches": True,
        "business_acceptance": False,
    }
    for key, expected_value in restore_requirements.items():
        require(restore.get(key) == expected_value, f"restore evidence {key} does not match backup evidence")
    checkpoint_match = re.fullmatch(r"uat_([1-9][0-9]*)", backup["checkpoint_id"])
    require(checkpoint_match is not None, "backup checkpoint_id is not a UAT run identity")
    require(restore.get("restore_database") == f"release_verify_{checkpoint_match.group(1)}", "restore database is not the isolated database for this backup run")
    require(restore.get("authorized_subscription_sample_id") == backup["authorized_subscription_sample_id"], "restore subscription sample reference differs from backup")
    # Hard false until the upstream official migrator implements and documents
    # this exact bounded, locked, checksum-verifying Selfhost interface.
    return {
        "status": "blocked",
        "reason_code": "UNSUPPORTED_SELFHOST_MIGRATOR_CONTRACT",
        "capability_supported": False,
        "manifest_status": "manifest_validated",
        "backup_restore_status": "shape_validated_only",
        "required_interface": [
            "official Accounts migrator invoked inside canonicalAccount with DSN supplied only through environment",
            "exact expected and target schema versions with a single-version upper bound",
            "reviewed migration checksum verified by the official migrator",
            "database advisory lock plus lock and statement timeout controls",
            "preflight and postflight clean-version checks that refuse dirty state without force-clear",
            "real encrypted backup and isolated-restore receipt bound to baseline_id and target_db_id",
        ],
    }


def main():
    payload = json.load(sys.stdin)
    print(json.dumps(validate(payload), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
