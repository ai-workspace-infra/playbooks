#!/usr/bin/env python3
"""Fail-closed validation for the reusable workflow's JSON contract."""

import json
import os
import re
import sys


def fail(message):
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


if os.environ.get("REQUESTED_ENVIRONMENT") != "uat":
    fail("environment must be exactly 'uat'; PROD writes are not supported")

try:
    config = json.loads(os.environ.get("CONFIG_JSON", "{}"))
except json.JSONDecodeError as exc:
    fail(f"config_json is not valid JSON: {exc.msg}")

if not isinstance(config, dict):
    fail("config_json must be a JSON object")
if config.get("confirm_legacy_import") is not True:
    fail("config.confirm_legacy_import must be explicitly true")
for field in ("environment", "vault_env_path", "target_environment"):
    if field in config and config[field] != "uat":
        fail(f"config.{field} must be exactly 'uat'")
for field in (
    "migration_scope",
    "toolkit_action",
    "accounts_transport",
    "accounts_source_backend",
    "accounts_target_backend",
    "accounts_migration_mode",
    "supabase_target_connection_mode",
    "supabase_project_ref",
    "supabase_vault_path",
    "supabase_source_vault_path",
    "supabase_target_dsn_key",
    "supabase_source_tunnel_host",
    "accounts_source_host",
    "accounts_target_host",
    "accounts_email_filter",
):
    if field in config and not isinstance(config[field], str):
        fail(f"config.{field} must be a string")
for field in ("supabase_metadata_dry_run", "supabase_target_confirm_replace"):
    if field in config and not isinstance(config[field], bool):
        fail(f"config.{field} must be a JSON boolean")
if config.get("supabase_target_confirm_replace", False) is True:
    fail("config.supabase_target_confirm_replace=true is prohibited")
if "caller_run_id" in config:
    caller_run_id = config["caller_run_id"]
    if isinstance(caller_run_id, bool) or not re.fullmatch(r"[0-9]{1,20}", str(caller_run_id)):
        fail("config.caller_run_id must contain 1-20 decimal digits")
else:
    caller_run_id = ""
correlation_id = os.environ.get("CORRELATION_ID", "")
if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", correlation_id):
    fail("correlation_id must be 1-100 letters, digits, dots, underscores, or hyphens")
strategy = config.get("supabase_target_existing_strategy", "reject")
if strategy == "replace_public":
    fail("supabase_target_existing_strategy=replace_public is prohibited")
if strategy not in ("reject", "accounts_merge"):
    fail("supabase_target_existing_strategy must be reject or accounts_merge")
if config.get("migration_scope", "accounts") != "accounts":
    fail("only Accounts data import is supported by this reusable workflow")
if config.get("toolkit_action", "migrate") != "migrate":
    fail("only the Accounts migrate action is supported")
if config.get("accounts_transport", "ssh") not in ("ssh", "direct"):
    fail("accounts_transport must be ssh or direct")
if config.get("accounts_source_backend", "supabase") not in ("vps", "supabase"):
    fail("accounts_source_backend must be vps or supabase")
if config.get("accounts_target_backend", "vps") not in ("vps", "supabase"):
    fail("accounts_target_backend must be vps or supabase")
target = config.get("accounts_target_backend", "vps")
mode = config.get("accounts_migration_mode", "data")
if mode not in ("data", "metadata", "metadata_and_data"):
    fail("accounts_migration_mode must be data, metadata, or metadata_and_data")
if (target, mode) not in (("vps", "data"), ("supabase", "metadata"), ("supabase", "metadata_and_data")):
    fail(f"unsupported migration combination: target={target}, mode={mode}")
if strategy == "accounts_merge" and (target, mode) != ("supabase", "metadata_and_data"):
    fail("accounts_merge requires a Supabase target and metadata_and_data mode")
if config.get("supabase_target_connection_mode", "session_pooler") not in ("session_pooler", "direct"):
    fail("supabase_target_connection_mode must be session_pooler or direct")
for field in ("supabase_vault_path", "supabase_source_vault_path"):
    value = config.get(field)
    if value is not None and (
        not isinstance(value, str)
        or not re.fullmatch(r"kv/data/uat/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
    ):
        fail(f"{field} must be a Vault KV v2 path under kv/data/uat")
for field in ("supabase_target_dsn_key",):
    value = config.get(field)
    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
    ):
        fail(f"{field} must be a simple Vault field name")

if config.get("accounts_transport") == "direct" and config.get("accounts_target_host"):
    if config["accounts_target_host"] != "web-saas-uat" or not caller_run_id:
        fail("target tunnel requires web-saas-uat and a successful selfhost caller_run_id")

output_path = os.environ.get("GITHUB_OUTPUT")
if output_path:
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"caller_run_id={caller_run_id}\n")

print("Request guard passed; no database connection was opened.")
