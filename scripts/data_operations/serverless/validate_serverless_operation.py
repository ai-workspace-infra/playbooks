#!/usr/bin/env python3
"""Fail-closed input gates for the extracted Serverless database workflow."""

import json
import os
import re
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    environment = os.environ.get("REQUESTED_ENVIRONMENT", "")
    operation = os.environ.get("REQUESTED_OPERATION", "")
    correlation = os.environ.get("CORRELATION_ID", "")
    release_tag = os.environ.get("RELEASE_TAG", "")
    try:
        config = json.loads(os.environ.get("CONFIG_JSON", "{}"))
    except json.JSONDecodeError as error:
        raise ValueError("config_json must be valid JSON") from error

    require(environment in {"uat", "prod"}, "environment must be uat or prod")
    if operation == "init-schema":
        raise ValueError("init-schema is retired; this workflow does not bootstrap schemas")
    require(operation in {"checkpoint", "probe", "baseline", "migrate"}, "unsupported operation")
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", correlation)),
            "correlation_id must contain only letters, digits, dot, underscore, or hyphen")
    require(isinstance(config, dict), "config_json must be a JSON object")

    if operation in {"baseline", "migrate"}:
        require(environment == "uat", f"{operation} is UAT-only")
    if operation in {"checkpoint", "baseline", "migrate"}:
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", release_tag)),
                "release_tag is required and must be a safe 1-64 character tag")

    if operation in {"baseline", "migrate"}:
        checkpoint_run_id = str(config.get("checkpoint_run_id", ""))
        require(bool(re.fullmatch(r"[0-9]+", checkpoint_run_id)),
                "checkpoint_run_id is required; checkpoint metadata alone does not prove a /data restore")
        artifact = config.get("backup_receipt")
        require(isinstance(artifact, dict),
                "checkpoint_run_id alone is not restore proof; provide backup_receipt with the sanitized trusted artifact locator before credentials are loaded")
        require(str(artifact.get("source_run_id", "")) == checkpoint_run_id,
                "backup_receipt source_run_id must equal checkpoint_run_id")
        require(artifact.get("source_repository") == os.environ.get("GITHUB_REPOSITORY"),
                "backup receipt must originate from this trusted caller repository")
        require(bool(re.fullmatch(r"[0-9]+", str(artifact.get("artifact_id", "")))),
                "backup_receipt artifact_id must be numeric")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("backup_sha256", "")))),
                "backup_receipt requires a lowercase SHA-256")
        require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(artifact.get("artifact_name", "")))),
                "backup_receipt requires a safe artifact name")
        require(artifact.get("artifact_name") == "serverless-database-receipt",
                "backup_receipt must identify the sanitized serverless-database-receipt artifact")

    if operation == "checkpoint":
        require(isinstance(config.get("require_durable_checkpoint"), bool),
                "require_durable_checkpoint must be a JSON boolean")

    if operation == "migrate":
        require(isinstance(config.get("repair_schema"), bool),
                "repair_schema must be a JSON boolean")
        require(bool(re.fullmatch(r"(uat-)?daily-build-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-r[1-9][0-9]*", release_tag)),
                "migrate requires an immutable Accounts release_tag")
        require(str(config.get("expected_schema_version", "")).isdigit(),
                "expected_schema_version is required")
        require(str(config.get("target_schema_version", "")).isdigit()
                and int(config["target_schema_version"]) > int(config["expected_schema_version"]),
                "target_schema_version must be greater than expected_schema_version")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", str(config.get("migration_sha256", "")))),
                "migration_sha256 must be a lowercase SHA-256")
        if config.get("repair_schema") is True:
            require(release_tag == "daily-build-2026.10.04-r3",
                    "repair_schema only supports the reviewed immutable Accounts tag in this source snapshot")
            require(config.get("accounts_source_sha") == "c2c343fe2c91bb31e7f7f9b4fa60512a66e2b4c9",
                    "repair_schema requires the reviewed immutable Accounts source SHA")
            require(str(config["expected_schema_version"]) == "2026092703"
                    and str(config["target_schema_version"]) == "2026092801"
                    and config["migration_sha256"] == "d066e223641b4eccbb65a00dce70f717b6dce02491d1d54edc1099baf2071433",
                    "repair_schema version/checksum differs from the reviewed source snapshot")

    if operation == "checkpoint" and config.get("require_durable_checkpoint") is True:
        require(environment == "uat", "durable S3 checkpoints are restricted to UAT")
        require(bool(re.fullmatch(r"(uat-)?daily-build-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-r[1-9][0-9]*", release_tag)),
                "durable checkpoint requires an immutable UAT snapshot tag")

    print(f"Validated {operation} request for {environment}; correlation_id={correlation}.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(2)
