#!/usr/bin/env python3
"""Rebuild dnsmasq hosts from the latest signed Gateway WireGuard config."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


DEVICE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
DNS_NAME = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(?:\.(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*$")


def dns_label(device_id: str) -> str:
    if not DEVICE_ID.fullmatch(device_id):
        raise ValueError(f"invalid peer device ID: {device_id!r}")
    label = re.sub(r"[._]+", "-", device_id.lower()).strip("-")
    if not label:
        raise ValueError("peer device ID does not produce a DNS label")
    if len(label) > 63:
        suffix = hashlib.sha256(device_id.encode()).hexdigest()[:8]
        label = f"{label[:54].rstrip('-')}-{suffix}"
    return label


def overlay_ip(value: str) -> str:
    interface = ipaddress.ip_interface(value)
    if interface.version != 4 or interface.network.prefixlen != 32 or interface.ip.is_unspecified:
        raise ValueError(f"WireGuard address must be an overlay IPv4 /32: {value!r}")
    return str(interface.ip)


def parse_wireguard_config(raw: str) -> tuple[str, dict[str, str]]:
    gateway_ip = ""
    peers: dict[str, str] = {}
    current_device = ""
    current_allowed = ""
    section = ""

    def save_peer() -> None:
        if current_device and current_allowed:
            if current_device in peers:
                raise ValueError(f"duplicate WireGuard peer device ID: {current_device}")
            peers[current_device] = overlay_ip(current_allowed.split(",", 1)[0].strip())

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "peer":
                save_peer()
            section = "interface" if stripped.lower() == "[interface]" else "peer" if stripped.lower() == "[peer]" else "other"
            current_device = ""
            current_allowed = ""
            continue
        if stripped.startswith("# DeviceID =") and section == "peer":
            current_device = stripped.split("=", 1)[1].strip()
            continue
        if "=" not in stripped or stripped.startswith("#"):
            continue
        key, value = (part.strip() for part in stripped.split("=", 1))
        if section == "interface" and key.lower() == "address":
            gateway_ip = overlay_ip(value.split(",", 1)[0].strip())
        elif section == "peer" and key.lower() == "allowedips":
            current_allowed = value

    if section == "peer":
        save_peer()
    if not gateway_ip:
        raise ValueError("signed WireGuard configuration is missing the Gateway overlay address")
    return gateway_ip, peers


def build_hosts(raw_config: str, zone: str, gateway_id: str, aliases: list[dict[str, str]]) -> str:
    if not DNS_NAME.fullmatch(zone):
        raise ValueError(f"invalid private DNS zone: {zone!r}")
    if not DEVICE_ID.fullmatch(gateway_id):
        raise ValueError(f"invalid Gateway device ID: {gateway_id!r}")
    gateway_ip, peers = parse_wireguard_config(raw_config)
    records: dict[str, str] = {}
    def add_record(name: str, address: str) -> None:
        if not DNS_NAME.fullmatch(name):
            raise ValueError(f"generated DNS name exceeds DNS limits: {name!r}")
        previous = records.get(name)
        if previous is not None and previous != address:
            raise ValueError(f"DNS name {name!r} maps to more than one overlay address")
        records[name] = address

    add_record(f"{dns_label(gateway_id)}.{zone}", gateway_ip)
    for device_id, address in peers.items():
        add_record(f"{dns_label(device_id)}.{zone}", address)

    for alias in aliases:
        name = str(alias.get("name", "")).strip().lower()
        device_id = str(alias.get("device_id", "")).strip()
        if not DNS_NAME.fullmatch(name) or "." not in name or not DEVICE_ID.fullmatch(device_id):
            raise ValueError(f"invalid DNS alias declaration: {alias!r}")
        address = peers.get(device_id)
        if address is None:
            # An alias only exists while its enrolled One appears in the
            # current signed Gateway peer snapshot.
            continue
        add_record(name, address)

    by_address: dict[str, list[str]] = {}
    for name, address in records.items():
        by_address.setdefault(address, []).append(name)
    return "".join(f"{address} {' '.join(sorted(names))}\n" for address, names in sorted(by_address.items()))


def write_if_changed(path: Path, contents: str) -> bool:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if current == contents:
        return False
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wireguard-config", required=True, type=Path)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--hosts", required=True, type=Path)
    parser.add_argument("--aliases", required=True, type=Path)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--service", default="dnsmasq")
    args = parser.parse_args()

    raw_config = args.wireguard_config.read_text(encoding="utf-8")
    aliases = json.loads(args.aliases.read_text(encoding="utf-8"))
    if not isinstance(aliases, list):
        raise ValueError("DNS alias file must contain a JSON array")
    changed = write_if_changed(args.hosts, build_hosts(raw_config, args.zone, args.gateway_id, aliases))
    if changed and args.reload:
        subprocess.run(["systemctl", "kill", "--signal=HUP", args.service], check=True)
    print("XConnect DNS records updated" if changed else "XConnect DNS records unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
