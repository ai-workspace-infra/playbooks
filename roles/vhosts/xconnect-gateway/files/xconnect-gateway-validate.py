#!/usr/bin/env python3
"""Validate the safe XConnect-One gateway shadow-mode bootstrap contract."""

import argparse
import json
import pathlib
import sys


REQUIRED_CAPABILITIES = {
    "peer.validate",
    "peer.render",
    "peer.read-back",
    "policy.validate",
    "policy.render",
    "relay.validate",
    "relay.render",
    "diagnostics.collect",
}


def load_object(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(config: dict, provider: dict) -> None:
    require(config.get("schema_version") == 1, "config schema_version must be 1")
    require(config.get("mode") == "shadow", "config mode must be shadow")
    require(config.get("apply", {}).get("enabled") is False, "runtime apply must be disabled")
    require(config.get("runtime", {}).get("proxy_core") == "xray", "proxy core must be xray")
    for key in ("xray_binary", "xray_config", "wireguard_config"):
        value = config.get("runtime", {}).get(key, "")
        require(pathlib.PurePosixPath(value).is_absolute(), f"runtime {key} must be absolute")
    require(
        config.get("snapshots", {}).get("empty_peer_snapshot") == "reject",
        "empty peer snapshots must be rejected",
    )

    require(provider.get("schema_version") == 1, "provider schema_version must be 1")
    require(provider.get("id") == "xconnect-one", "provider id must be xconnect-one")
    require(provider.get("mode") == "shadow", "provider mode must be shadow")
    require(provider.get("runtime", {}).get("proxy_core") == "xray", "provider proxy core must be xray")
    require(provider.get("backends", {}).get("relay") == "xray", "relay backend must be xray")
    require(
        provider.get("permissions", {}).get("apply_runtime") is False,
        "shadow provider cannot apply runtime state",
    )
    capabilities = set(provider.get("capabilities", []))
    missing = sorted(REQUIRED_CAPABILITIES - capabilities)
    require(not missing, f"provider capabilities missing: {', '.join(missing)}")

    config_min = config.get("snapshots", {}).get("minimum_schema")
    config_max = config.get("snapshots", {}).get("maximum_schema")
    provider_min = provider.get("snapshot_schema", {}).get("minimum")
    provider_max = provider.get("snapshot_schema", {}).get("maximum")
    require((config_min, config_max) == (provider_min, provider_max), "snapshot schema ranges differ")
    require(isinstance(config_min, int) and isinstance(config_max, int), "snapshot schema range must be integers")
    require(1 <= config_min <= config_max, "snapshot schema range is invalid")

    for key in ("candidate_dir", "last_known_good_dir", "evidence_dir"):
        value = config.get("snapshots", {}).get(key, "")
        require(pathlib.PurePosixPath(value).is_absolute(), f"{key} must be absolute")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--provider", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        validate(load_object(args.config), load_object(args.provider))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"xconnect gateway contract rejected: {exc}", file=sys.stderr)
        return 1
    print("xconnect gateway shadow contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
