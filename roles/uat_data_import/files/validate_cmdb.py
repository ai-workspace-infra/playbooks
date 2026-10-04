#!/usr/bin/env python3
"""Require the caller artifact to contain valid IPs for configured SSH hosts."""

import ipaddress
import json
import os
import sys


path = os.path.join(os.getcwd(), "cmdb", "cmdb.json")
try:
    with open(path, encoding="utf-8") as stream:
        cmdb = json.load(stream)
    config = json.loads(os.environ.get("CONFIG_JSON", "{}"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"caller CMDB is missing or invalid: {exc}")

if not isinstance(cmdb, dict) or not isinstance(config, dict):
    raise SystemExit("caller CMDB and migration config must be JSON objects")

for field in ("accounts_source_host", "accounts_target_host"):
    host = config.get(field, "")
    if not host:
        continue
    entry = cmdb.get(host)
    address = entry.get("ip") if isinstance(entry, dict) else None
    if not isinstance(address, str):
        raise SystemExit(f"caller CMDB has no IP entry for configured {field}")
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise SystemExit(f"caller CMDB has an invalid IP for configured {field}")

print("Caller CMDB validated for configured migration hosts.")
