#!/usr/bin/env python3
"""Validate the safe XConnect-One gateway shadow-mode bootstrap contract."""

import argparse
import json
import pathlib
import sys
from datetime import datetime


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


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(config: dict, provider: dict) -> None:
    require(config.get("schema_version") == 1, "config schema_version must be 1")
    require(config.get("mode") == "shadow", "config mode must be shadow")
    require(config.get("apply", {}).get("enabled") is False, "runtime apply must be disabled")
    require(config.get("runtime", {}).get("proxy_core") == "xray", "proxy core must be xray")
    for key in ("xray_binary", "xray_config", "wireguard_config"):
        value = config.get("runtime", {}).get(key, "")
        require(pathlib.PurePosixPath(value).is_absolute(), f"runtime {key} must be absolute")
    require(
        config.get("snapshots", {}).get("empty_peer_snapshot") == "require-explicit-override",
        "empty peer snapshots must require an explicit safety override",
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
    require(
        provider.get("runtime", {}).get("proxy_core_version")
        == config.get("runtime", {}).get("proxy_core_version"),
        "provider and gateway Xray-core versions differ",
    )

    for key in ("candidate_dir", "last_known_good_dir", "evidence_dir"):
        value = config.get("snapshots", {}).get(key, "")
        require(pathlib.PurePosixPath(value).is_absolute(), f"{key} must be absolute")


def validate_snapshot(snapshot: dict) -> None:
    require(snapshot.get("schema_version") == 1, "snapshot schema_version must be 1")
    require(snapshot.get("proxy_core") == "xray", "snapshot proxy core must be xray")
    require(bool(snapshot.get("snapshot_id")), "snapshot_id is required")
    require(bool(snapshot.get("node_id")), "snapshot node_id is required")

    generation = snapshot.get("generation")
    previous = snapshot.get("expected_previous_generation")
    require(isinstance(generation, int), "snapshot generation must be an integer")
    require(isinstance(previous, int), "expected_previous_generation must be an integer")
    require(generation > previous, "generation must advance expected_previous_generation")

    issued_at = parse_time(snapshot.get("issued_at", ""))
    expires_at = parse_time(snapshot.get("expires_at", ""))
    require(expires_at > issued_at, "expires_at must be later than issued_at")

    safety = snapshot.get("safety", {})
    allow_empty = safety.get("allow_empty_peers")
    removal_limit = safety.get("max_peer_removal_percent")
    require(isinstance(allow_empty, bool), "safety.allow_empty_peers must be boolean")
    require(
        isinstance(removal_limit, (int, float)) and 0 <= removal_limit <= 100,
        "safety.max_peer_removal_percent must be between 0 and 100",
    )

    peers = snapshot.get("wireguard", {}).get("peers")
    require(isinstance(peers, list), "wireguard.peers must be an array")
    require(bool(peers) or allow_empty, "empty peers require an explicit safety override")
    device_ids = [peer.get("device_id") for peer in peers if isinstance(peer, dict)]
    require(len(device_ids) == len(peers), "every peer must be an object with device_id")
    require(len(device_ids) == len(set(device_ids)), "peer device_id values must be unique")

    require(snapshot.get("relay", {}).get("transport") == "vless-tls-xudp", "relay transport is invalid")
    require(snapshot.get("policy", {}).get("backend") == "nftables", "policy backend must be nftables")
    require(snapshot.get("signature", {}).get("algorithm") == "Ed25519", "signature algorithm must be Ed25519")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--provider", required=True, type=pathlib.Path)
    parser.add_argument("--snapshot", type=pathlib.Path)
    args = parser.parse_args()
    try:
        validate(load_object(args.config), load_object(args.provider))
        if args.snapshot:
            validate_snapshot(load_object(args.snapshot))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"xconnect gateway contract rejected: {exc}", file=sys.stderr)
        return 1
    print("xconnect gateway shadow contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
