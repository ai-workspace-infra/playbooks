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

requested_env = str(config.get("environment") or os.environ.get("REQUESTED_ENVIRONMENT") or "").strip().lower()
cmdb_env = str(cmdb.get("environment") or "").strip().lower()
if requested_env and cmdb_env and cmdb_env != "unknown" and cmdb_env != requested_env:
    raise SystemExit(f"caller CMDB environment {cmdb_env!r} conflicts with requested environment {requested_env!r}")

hosts_map = cmdb.get("hosts") if isinstance(cmdb.get("hosts"), dict) else {}
serverless_map = cmdb.get("serverless_resources") if isinstance(cmdb.get("serverless_resources"), dict) else {}

for field in ("accounts_source_host", "accounts_target_host"):
    host = config.get(field, "")
    if not host:
        continue
    if host in serverless_map and host not in hosts_map and host not in cmdb:
        raise SystemExit(f"configured {field} {host!r} is a serverless resource without SSH/IP")
    entry = hosts_map.get(host) if host in hosts_map else cmdb.get(host)
    if isinstance(entry, dict) and str(entry.get("runtime_form") or "").lower() == "serverless":
        raise SystemExit(f"configured {field} {host!r} is a serverless resource without SSH/IP")
    address = (entry.get("ip") or entry.get("primary_ip") or entry.get("ssh_host")) if isinstance(entry, dict) else None
    if not isinstance(address, str):
        raise SystemExit(f"caller CMDB has no IP entry for configured {field}")
    try:
        ip_obj = ipaddress.ip_address(address)
        if ip_obj.is_unspecified or ip_obj.is_loopback:
            raise SystemExit(f"caller CMDB has a placeholder/loopback IP for configured {field}")
    except ValueError:
        raise SystemExit(f"caller CMDB has an invalid IP for configured {field}")

print("Caller CMDB validated for configured migration hosts.")
