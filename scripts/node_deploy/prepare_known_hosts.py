#!/usr/bin/env python3
"""Verify live SSH host keys against reviewed GitOps pins before Ansible."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from render_inventory import validate


def host_label(address: str, port: int) -> str:
    """Return the known_hosts name OpenSSH uses for this address and port."""
    return address if port == 22 else f"[{address}]:{port}"


def verify_scan(address: str, expected: str, scan: str) -> str:
    matches = []
    for line in scan.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "ssh-ed25519" and parts[0] == address:
            matches.append(parts[2])
    if matches != [expected]:
        raise ValueError(f"SSH host key for {address} does not match reviewed GitOps pin")
    return f"{address} ssh-ed25519 {expected}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    doc = validate(json.loads(args.contract.read_text(encoding="utf-8")))
    known_hosts = []
    for node in doc["spec"]["nodes"]:
        port = node.get("ssh_port", 22)
        expected = node.get("ssh_host_ed25519")
        if not expected:
            raise SystemExit(f"{node['id']} has no reviewed Ed25519 SSH host key")
        scan = subprocess.run(
            ["ssh-keyscan", "-T", "10", "-t", "ed25519", "-p", str(port), node["address"]],
            capture_output=True,
            text=True,
            check=True,
        )
        known_hosts.append(verify_scan(host_label(node["address"], port), expected, scan.stdout))
    args.output.write_text("\n".join(known_hosts) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(f"verified reviewed SSH host keys for {len(known_hosts)} nodes")


if __name__ == "__main__":
    main()
