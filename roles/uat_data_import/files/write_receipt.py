#!/usr/bin/env python3
"""Write a minimal, non-sensitive import execution receipt."""

import json
import os
import re


try:
    config = json.loads(os.environ.get("CONFIG_JSON", "{}"))
except json.JSONDecodeError:
    config = {}
if not isinstance(config, dict):
    config = {}

target = config.get("accounts_target_backend", "vps")
if target not in ("vps", "supabase"):
    target = "vps"
migration_mode = config.get("accounts_migration_mode", "data")
if migration_mode not in ("data", "metadata", "metadata_and_data"):
    migration_mode = "data"
if target == "supabase" and config.get("supabase_target_existing_strategy") == "accounts_merge":
    source = config.get("accounts_source_backend", "supabase")
else:
    source = config.get("accounts_source_backend", "supabase")
if source not in ("vps", "supabase"):
    source = config.get("accounts_source_backend", "supabase")
transport = "ssh"
if target == "vps":
    transport = config.get("accounts_transport", "ssh")
elif source == "supabase":
    transport = "direct"
if transport not in ("ssh", "direct"):
    transport = "ssh"
mode = f"accounts/{source}-to-{target}/{migration_mode}/{transport}"

correlation = os.environ.get("CORRELATION_ID", "")
if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", correlation):
    correlation = "rejected"

receipt = {
    "schema": "uat-data-import/v1",
    "environment": "uat" if os.environ.get("REQUESTED_ENVIRONMENT") == "uat" else "rejected",
    "correlation_id": correlation,
    "mode": mode,
    "dry_run": os.environ.get("REQUESTED_DRY_RUN") == "true",
    "success": os.environ.get("EXECUTION_SUCCESS") == "true",
}
runtime_path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "uat-import-runtime.json")
try:
    with open(runtime_path, encoding="utf-8") as stream:
        runtime = json.load(stream)
    phases = {"credentials", "target_tunnel", "source_export", "target_preview", "target_apply"}
    categories = {"success", "execution_failed", "ssh_authentication", "ssh_timeout", "database_authentication", "database_permission", "database_schema", "database_connection", "execution_timeout"}
    if runtime.get("phase") in phases and runtime.get("category") in categories and re.fullmatch(r"(?:[A-Z0-9]{5})?", runtime.get("sqlstate", "")):
        receipt["runtime"] = {key: runtime[key] for key in ("phase", "category", "sqlstate")}
except (OSError, ValueError, TypeError, KeyError):
    pass
print(json.dumps(receipt, sort_keys=True))
with open("uat-data-import-receipt.json", "w", encoding="utf-8") as stream:
    json.dump(receipt, stream, sort_keys=True)
    stream.write("\n")
