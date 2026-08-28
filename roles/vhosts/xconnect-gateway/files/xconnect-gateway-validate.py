#!/usr/bin/env python3
"""Validate XConnect-One shadow/apply bootstrap contracts fail closed."""

import argparse
import json
import pathlib
import re
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
APPLY_CAPABILITIES = {"peer.apply", "policy.apply", "relay.apply", "transaction.rollback"}


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
    mode = config.get("mode")
    apply_enabled = config.get("apply", {}).get("enabled")
    require(mode in {"shadow", "apply"}, "config mode must be shadow or apply")
    require(apply_enabled is (mode == "apply"), "runtime apply flag must exactly match mode")
    identity = config.get("identity", {})
    require(identity.get("node_id") == config.get("node_id"), "identity node_id must match gateway node_id")
    require(
        identity.get("credential_type") == "short-lived-bearer-file",
        "identity must use a short-lived bearer credential file",
    )
    require(
        identity.get("credential_file") == config.get("control_plane", {}).get("credentials_file"),
        "identity and control-plane credential references differ",
    )
    require(config.get("control_plane", {}).get("api_version") == "v1", "control-plane api_version must be v1")
    require(bool(config.get("control_plane", {}).get("snapshot_signing_key_id")), "snapshot signing key ID is required")
    require(config.get("runtime", {}).get("proxy_core") == "xray", "proxy core must be xray")
    authority = config.get("authority", {})
    require(
        authority.get("projection_source") in {"static-shadow", "accounts-only"},
        "projection authority must be static-shadow or accounts-only",
    )
    require(
        pathlib.PurePosixPath(authority.get("readiness_evidence_file", "")).is_absolute(),
        "accounts-only readiness evidence path must be absolute",
    )
    require(
        authority.get("projection_source") != "accounts-only" or apply_enabled is True,
        "accounts-only authority requires explicit runtime apply",
    )
    for key in ("xray_binary", "xray_config", "wireguard_config"):
        value = config.get("runtime", {}).get(key, "")
        require(pathlib.PurePosixPath(value).is_absolute(), f"runtime {key} must be absolute")
    require(
        config.get("snapshots", {}).get("empty_peer_snapshot") == "require-explicit-override",
        "empty peer snapshots must require an explicit safety override",
    )

    require(provider.get("schema_version") == 1, "provider schema_version must be 1")
    require(provider.get("id") == "xconnect-one", "provider id must be xconnect-one")
    require(provider.get("mode") == mode, "provider mode must match config")
    require(provider.get("runtime", {}).get("proxy_core") == "xray", "provider proxy core must be xray")
    require(provider.get("backends", {}).get("relay") == "xray", "relay backend must be xray")
    permissions = provider.get("permissions", {})
    require(permissions.get("apply_runtime") is apply_enabled, "provider apply permission must match mode")
    require(
        permissions.get("linux_capabilities") == (["CAP_NET_ADMIN"] if apply_enabled else []),
        "only apply mode may request CAP_NET_ADMIN",
    )
    capabilities = set(provider.get("capabilities", []))
    missing = sorted(REQUIRED_CAPABILITIES - capabilities)
    require(not missing, f"provider capabilities missing: {', '.join(missing)}")
    require(not apply_enabled or not (APPLY_CAPABILITIES - capabilities), "apply capabilities are incomplete")
    require(apply_enabled or not (APPLY_CAPABILITIES & capabilities), "shadow provider exposes apply capabilities")

    if apply_enabled:
        runtime = config.get("runtime", {})
        for key in (
            "wireguard_binary",
            "nftables_binary",
            "ip_binary",
            "relay_credential_dir",
            "relay_tls_certificate_file",
            "relay_tls_private_key_file",
        ):
            require(pathlib.PurePosixPath(runtime.get(key, "")).is_absolute(), f"apply runtime {key} must be absolute")
        require(runtime.get("xray_api_endpoint", "").startswith(("127.0.0.1:", "[::1]:")), "Xray API must be loopback")
        require(config.get("apply", {}).get("relay_enabled") is True, "full runtime apply requires relay transaction")
        for key in ("lock_file", "transaction_dir", "runtime_last_known_good", "runtime_secret_last_known_good"):
            require(pathlib.PurePosixPath(config.get("apply", {}).get(key, "")).is_absolute(), f"apply {key} must be absolute")

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
    health = config.get("health", {})
    require(health.get("listen_host") in {"127.0.0.1", "::1"}, "health endpoint must be loopback-only")
    require(
        isinstance(health.get("listen_port"), int) and 1 <= health["listen_port"] <= 65535,
        "health listen_port is invalid",
    )
    require(str(health.get("path", "")).startswith("/"), "health path must be absolute")
    logging = config.get("logging", {})
    redact_fields = set(logging.get("redact_fields", []))
    require(logging.get("format") == "json", "gateway logs must use structured JSON")
    require(
        {"authorization", "credential", "token", "signature.value"} <= redact_fields,
        "gateway log redaction fields are incomplete",
    )


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


def validate_xray_base(base: dict, config: dict) -> None:
    runtime = config.get("runtime", {})
    api = base.get("api", {})
    require(api.get("tag") == "xconnect-one-api", "Xray API tag is not dedicated")
    require(set(api.get("services", [])) == {"HandlerService", "StatsService"}, "Xray Handler/Stats services are required")
    inbounds = base.get("inbounds", [])
    require(len(inbounds) == 1 and inbounds[0].get("tag") == "xconnect-one-api", "base config may expose only the loopback API inbound")
    require(inbounds[0].get("listen") in {"127.0.0.1", "::1"}, "Xray API inbound must be loopback-only")
    outbounds = {value.get("tag"): value.get("protocol") for value in base.get("outbounds", [])}
    require(outbounds == {"xconnect-one-wg": "freedom", "xconnect-one-block": "blackhole"}, "Xray outbounds must be restricted direct+block")
    rules = base.get("routing", {}).get("rules", [])
    relay_tag = runtime.get("xray_inbound_tag")
    allow = [rule for rule in rules if rule.get("inboundTag") == [relay_tag] and rule.get("outboundTag") == "xconnect-one-wg"]
    block = [rule for rule in rules if rule.get("inboundTag") == [relay_tag] and rule.get("outboundTag") == "xconnect-one-block"]
    require(len(allow) == 1 and allow[0].get("network") == "udp", "relay direct route must be UDP-only")
    require(allow[0].get("port") == runtime.get("wireguard_listen_port"), "relay direct route must target the bound WireGuard port")
    require(allow[0].get("ip") == ["127.0.0.1"], "relay direct route must target only the v1 loopback WireGuard endpoint")
    require(len(block) == 1 and rules[-1] == block[0], "relay catch-all block rule must be last")


def validate_xray_baseline(baseline: dict, config: dict) -> None:
    require(set(baseline) == {"inbounds"}, "Xray AddInbound baseline may contain only inbounds")
    inbounds = baseline.get("inbounds", [])
    require(len(inbounds) == 1, "Xray baseline must contain exactly one dedicated inbound")
    inbound = inbounds[0]
    runtime = config.get("runtime", {})
    require(inbound.get("tag") == runtime.get("xray_inbound_tag"), "Xray baseline tag differs from dedicated runtime tag")
    require(inbound.get("listen") == runtime.get("relay_listen_host"), "Xray baseline listen host differs from node binding")
    require(inbound.get("port") == runtime.get("relay_listen_port"), "Xray baseline listen port differs from node binding")
    require(inbound.get("protocol") == "vless", "Xray baseline must use VLESS")
    require(inbound.get("settings", {}).get("decryption") == "none", "Xray baseline VLESS decryption must be none")
    stream = inbound.get("streamSettings", {})
    require(stream.get("network") == "tcp" and stream.get("security") == "tls", "Xray baseline must match the v1 client TCP+TLS transport")
    require(bool(stream.get("tlsSettings", {}).get("certificates")), "Xray baseline TLS identity is required")


def validate_relay_credential(credential: dict, baseline: dict, config: dict) -> None:
    require(set(credential) == {"id", "certificate_file", "private_key_file"}, "relay credential fields are not strict")
    require(bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", credential.get("id", ""))), "relay credential ID must be a canonical UUID")
    runtime = config.get("runtime", {})
    require(credential.get("certificate_file") == runtime.get("relay_tls_certificate_file"), "relay certificate path differs from node binding")
    require(credential.get("private_key_file") == runtime.get("relay_tls_private_key_file"), "relay private-key path differs from node binding")
    clients = baseline.get("inbounds", [{}])[0].get("settings", {}).get("clients", [])
    require([value.get("id") for value in clients] == [credential.get("id")], "baseline and relay credential identities differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--provider", required=True, type=pathlib.Path)
    parser.add_argument("--snapshot", type=pathlib.Path)
    parser.add_argument("--xray-base", type=pathlib.Path)
    parser.add_argument("--xray-baseline", type=pathlib.Path)
    parser.add_argument("--relay-credential", type=pathlib.Path)
    args = parser.parse_args()
    try:
        validate(load_object(args.config), load_object(args.provider))
        if args.snapshot:
            validate_snapshot(load_object(args.snapshot))
        if args.xray_base:
            validate_xray_base(load_object(args.xray_base), load_object(args.config))
        if args.xray_baseline:
            validate_xray_baseline(load_object(args.xray_baseline), load_object(args.config))
        if args.relay_credential:
            require(bool(args.xray_baseline), "relay credential validation requires Xray baseline")
            validate_relay_credential(load_object(args.relay_credential), load_object(args.xray_baseline), load_object(args.config))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"xconnect gateway contract rejected: {exc}", file=sys.stderr)
        return 1
    print("xconnect gateway contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
