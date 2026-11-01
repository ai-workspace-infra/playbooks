#!/usr/bin/env python3
"""Shared SIT host and Vault response validation for feature-lane owners."""
import ipaddress
import json
import re
import urllib.request

TARGET = "dev-k3s-v5"

def require(condition, message):
    if not condition:
        raise ValueError(message)

def request(url, headers=None, data=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}, data=data), timeout=30) as response:
        return json.load(response) if response.status != 204 else {}

def validate_session(session):
    require(isinstance(session, dict), "invalid Vault auth response")
    require(0 < session.get("lease_duration", 0) <= 1200, "Vault session TTL is not bounded")
    require(session.get("token_type") in {"service", "batch"} and session.get("client_token"), "invalid Vault token type")
    for field in ("policies", "token_policies", "identity_policies"):
        policies = session.get(field, [])
        require(isinstance(policies, list) and "root" not in policies, "root Vault session is forbidden")
    return session

def validate_cmdb(data, instance_id):
    require(isinstance(data, dict) and set(data) == {TARGET}, "CMDB must contain exactly the Dev target")
    h = data[TARGET]
    require(isinstance(h, dict), "invalid CMDB host")
    require(str(h.get("instance_id")) == instance_id and instance_id.isdecimal() and int(instance_id) > 0, "instance ID mismatch")
    require(h.get("name") == TARGET and h.get("label") == TARGET, "CMDB target identity mismatch")
    ipaddress.IPv4Address(h["ip"])
    require("dev" in h.get("groups", []) and not {"prod", "production", "uat"}.intersection(h.get("groups", [])), "CMDB environment mismatch")
    v = h.get("host_vars", {})
    require(v.get("cloud_provider") == "akamai-cloud" and v.get("cloud_region") == "jp-tyo-3", "CMDB provider/region mismatch")
    require(v.get("k3s_platform_node_name") == TARGET and v.get("k3s_platform_write_kubeconfig_mode") == "0600", "missing Dev node/security inputs")
    # Preserve only the approved role inputs; arbitrary ansible_* connection or
    # interpreter overrides from an artifact must never become executable input.
    keys = ("cloud_provider", "cloud_region", "k3s_platform_k3s_version", "k3s_platform_node_name",
            "k3s_platform_cluster_name", "k3s_platform_write_kubeconfig_mode")
    require(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+", v.get("k3s_platform_k3s_version", "")), "invalid K3s version")
    return {TARGET: {"name": TARGET, "label": TARGET, "ip": h["ip"], "instance_id": h["instance_id"],
                     "groups": ["dev", "k3s"], "host_vars": {k: v[k] for k in keys if k in v}}}
