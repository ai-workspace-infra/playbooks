#!/usr/bin/env python3
"""Validate the reusable selfhost database operation before credential access."""

import json
import os
import re
import sys


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


environment = os.environ.get("REQUESTED_ENVIRONMENT", "")
operation = os.environ.get("REQUESTED_OPERATION", "")
if environment not in {"uat", "prod", "sit"}:
    fail("environment must be explicitly uat, prod, or sit")
if operation not in {"selfhost_probe", "selfhost_init", "selfhost_verify"}:
    fail("unsupported selfhost database operation")
try:
    config = json.loads(os.environ.get("CONFIG_JSON", "{}"))
except json.JSONDecodeError as exc:
    fail(f"config_json is invalid JSON: {exc.msg}")
if not isinstance(config, dict):
    fail("config_json must be a JSON object")

correlation = os.environ.get("CORRELATION_ID", "")
if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", correlation):
    fail("correlation_id must be a plain identifier")

if operation in {"selfhost_probe", "selfhost_init", "selfhost_verify"}:
    host = config.get("target_host")
    if host != f"web-saas-{environment}":
        fail(f"config_json.target_host must be exactly web-saas-{environment}")
if operation in {"selfhost_probe", "selfhost_init", "selfhost_verify"}:
    caller_run_id = config.get("caller_run_id")
    if not isinstance(caller_run_id, str) or not re.fullmatch(r"[1-9][0-9]*", caller_run_id):
        fail("config_json.caller_run_id must identify the caller workflow run")
if operation == "selfhost_probe":
    if config.get("action") not in {"baseline", "probe"}:
        fail("selfhost_probe requires action=baseline or action=probe")
    run_id = config.get("acceptance_run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", run_id):
        fail("selfhost_probe requires a plain acceptance_run_id")
if operation == "selfhost_verify":
    run_id = config.get("acceptance_run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", run_id):
        fail("selfhost_verify requires a plain acceptance_run_id")
    tag = os.environ.get("RELEASE_TAG", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", tag):
        fail("selfhost_verify requires an immutable release_tag")
    accounts_ref = os.environ.get("ACCOUNTS_REF", "")
    if accounts_ref != tag:
        fail("selfhost_verify requires accounts_ref to equal release_tag")
    baseline_run = config.get("baseline_data_run_id")
    if not isinstance(baseline_run, str) or not re.fullmatch(r"[1-9][0-9]*", baseline_run):
        fail("selfhost_verify requires the dispatched baseline_data_run_id")
    version = config.get("expected_schema_version")
    if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]*", version)):
        fail("expected_schema_version must be a positive integer string")
if operation == "selfhost_init":
    if environment != "uat":
        fail("selfhost_init is UAT-only")
    if os.environ.get("RELEASE_TAG", "") != os.environ.get("ACCOUNTS_REF", ""):
        fail("selfhost_init requires accounts_ref to equal release_tag")
    accounts_ref = os.environ.get("ACCOUNTS_REF", "")
    if not re.fullmatch(r"(?:uat-)?daily-build-\d{4}\.\d{2}\.\d{2}(?:-r[1-9]\d*)?|v\d[0-9.r-]*", accounts_ref):
        fail("selfhost_init requires an immutable Accounts release ref")
print(f"Validated {operation} request for {environment}; no database was contacted.")
