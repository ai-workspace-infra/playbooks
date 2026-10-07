#!/usr/bin/env python3
"""Read-only verifier for the effective Gateway or One XHTTP runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class ContractError(RuntimeError):
    pass


def load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractError(f"runtime JSON is unavailable: {path}") from error
    if not isinstance(value, dict):
        raise ContractError(f"runtime JSON must be an object: {path}")
    return value


def one_config(state_dir: Path) -> Path:
    active = load_object(state_dir / "runtime" / "active.json")
    raw = active.get("xray_config_path")
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise ContractError("One active runtime has no absolute xray_config_path")
    candidate = Path(raw)
    if ".." in candidate.parts or not candidate.is_file():
        raise ContractError("One active Xray configuration path is unsafe or unavailable")
    return candidate


def verify_one(config: dict, args: argparse.Namespace) -> str:
    inbounds = config.get("inbounds", [])
    outbounds = config.get("outbounds", [])

    def exact_vnext(item: dict) -> bool:
        vnext = item.get("settings", {}).get("vnext", [])
        return (isinstance(vnext, list) and len(vnext) == 1 and isinstance(vnext[0], dict)
                and vnext[0].get("address") == args.remote_address
                and vnext[0].get("port") == 443)

    inbound_ok = any(
        isinstance(item, dict)
        and item.get("listen") == "127.0.0.1"
        and item.get("port") == 51830
        and item.get("protocol") == "dokodemo-door"
        and item.get("settings", {}).get("network") == "udp"
        for item in inbounds
    )
    outbound_ok = any(
        isinstance(item, dict)
        and item.get("protocol") == "vless"
        and exact_vnext(item)
        and item.get("streamSettings", {}).get("network") == "xhttp"
        and item.get("streamSettings", {}).get("security") == "tls"
        and item.get("streamSettings", {}).get("tlsSettings", {}).get("serverName") == args.server_name
        and item.get("streamSettings", {}).get("xhttpSettings", {}).get("path") == args.xhttp_path
        and item.get("streamSettings", {}).get("xhttpSettings", {}).get("mode") == args.xhttp_mode
        and item.get("streamSettings", {}).get("xhttpSettings", {}).get("host") == args.xhttp_host
        for item in outbounds
    )
    if not inbound_ok or not outbound_ok:
        raise ContractError("One XHTTP runtime does not match the reviewed transport contract")
    return "one"


def verify_gateway(config: dict, args: argparse.Namespace) -> str:
    runtime_mode = ""
    for item in config.get("inbounds", []):
        if not isinstance(item, dict) or item.get("protocol") != "vless":
            continue
        stream = item.get("streamSettings", {})
        xhttp = stream.get("xhttpSettings", {})
        if (stream.get("network"), xhttp.get("path"), xhttp.get("mode"), xhttp.get("host")) != (
            "xhttp", args.xhttp_path, args.xhttp_mode, args.xhttp_host
        ):
            continue
        tls = stream.get("tlsSettings", {})
        if (item.get("listen") == "0.0.0.0" and item.get("port") == 443
                and stream.get("security") == "tls" and tls.get("rejectUnknownSni") is True):
            runtime_mode = "direct-tls"
        elif (item.get("listen") == "/run/xconnect-gateway/xray.sock,0660"
              and item.get("port") is None and stream.get("security") is None):
            runtime_mode = "caddy-unix"
    redirect_ok = any(
        isinstance(item, dict)
        and item.get("tag") == "xconnect-wireguard"
        and item.get("protocol") == "freedom"
        and item.get("settings", {}).get("redirect") == "127.0.0.1:51820"
        for item in config.get("outbounds", [])
    )
    if not runtime_mode or not redirect_ok:
        raise ContractError("Gateway XHTTP runtime does not match the reviewed transport contract")
    return runtime_mode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("gateway", "one"), required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--remote-address", default="")
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--xhttp-path", required=True)
    parser.add_argument("--xhttp-mode", choices=("auto", "packet-up", "stream-up"), required=True)
    parser.add_argument("--xhttp-host", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config_path = one_config(args.config_path) if args.role == "one" else args.config_path
        config = load_object(config_path)
        mode = verify_one(config, args) if args.role == "one" else verify_gateway(config, args)
    except ContractError as error:
        print(str(error))
        return 1
    print(f"xhttp_runtime=valid role={args.role} mode={mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
