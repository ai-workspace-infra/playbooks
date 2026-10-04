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
    source = "vps"
if source not in ("vps", "supabase"):
    source = "vps"
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
with open("uat-data-import-receipt.json", "w", encoding="utf-8") as stream:
    json.dump(receipt, stream, sort_keys=True)
    stream.write("\n")
