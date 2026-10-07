#!/usr/bin/env python3
"""Run exact-pair XConnect data-plane verification through Playbooks."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable
from urllib.parse import urlparse


class ContractError(RuntimeError):
    pass


def required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ContractError(f"{name} is required")
    return value


def runtime_path(environment: dict[str, str], value: str, label: str, *, exists: bool, private: bool) -> Path:
    root = Path(required(environment, "RUNNER_TEMP")).resolve(strict=True)
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ContractError(f"{label} must be an absolute non-symlink path below RUNNER_TEMP")
    try:
        resolved = candidate.resolve(strict=exists)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ContractError(f"{label} must remain below RUNNER_TEMP") from None
    if exists and not resolved.is_file():
        raise ContractError(f"{label} must be a regular file")
    if exists and private and resolved.stat().st_mode & 0o077:
        raise ContractError(f"{label} must not be accessible by group or other")
    return resolved


def exact_host(value: object, label: str) -> str:
    if not isinstance(value, str) or any(mark in value for mark in ("*", "?", "[", "]", ",", "/", " ")):
        raise ContractError(f"{label} must be one exact host")
    try:
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
            raise ContractError(f"{label} is not a routable exact target")
        return value
    except ValueError:
        pass
    if value.lower().rstrip(".") == "localhost" or not re.fullmatch(
            r"(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value):
        raise ContractError(f"{label} must be one exact IPv4 or DNS host")
    return value.rstrip(".")


def identifier(value: object, label: str, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ContractError(f"{label} is invalid")
    return value


def checked_run(command, **kwargs):
    return subprocess.run(command, check=False, text=True, capture_output=True, **kwargs)


def execute(environment: dict[str, str], runner: Callable[..., object] = checked_run) -> dict:
    contract_path = runtime_path(
        environment, required(environment, "XCONNECT_EVIDENCE_CONTRACT_FILE"),
        "XCONNECT_EVIDENCE_CONTRACT_FILE", exists=True, private=True,
    )
    receipt_path = runtime_path(
        environment, required(environment, "XCONNECT_EVIDENCE_RECEIPT_FILE"),
        "XCONNECT_EVIDENCE_RECEIPT_FILE", exists=False, private=False,
    )
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        gateway = contract["gateway"]
        one = contract["one"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ContractError("evidence contract has an invalid shape") from None
    if contract.get("environment") != "uat":
        raise ContractError("evidence contract is restricted to UAT")
    run_id = identifier(contract.get("run_id"), "run_id", r"xcl-[0-9]+-[0-9]+")
    network_id = identifier(contract.get("network_id"), "network_id", r"net_[A-Za-z0-9][A-Za-z0-9_-]{1,62}")
    device_id = identifier(one.get("device_id"), "one.device_id", r"[a-z0-9][a-z0-9._-]{0,127}")
    gateway_target = exact_host(gateway.get("target"), "gateway.target")
    one_target = exact_host(one.get("target"), "one.target")
    if gateway_target == one_target:
        raise ContractError("Gateway and One targets must be distinct")
    for node_name, node in (("gateway", gateway), ("one", one)):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", str(node.get("user", ""))):
            raise ContractError(f"{node_name}.user is invalid")
    try:
        gateway_ip = str(ipaddress.ip_address(str(gateway.get("overlay_ip", ""))))
    except ValueError:
        raise ContractError("gateway.overlay_ip is invalid") from None
    if not ipaddress.ip_address(gateway_ip).is_private:
        raise ContractError("gateway.overlay_ip must be a private address")
    server_name = exact_host(contract.get("transport_server_name"), "transport_server_name")
    if not re.search(r"\.", server_name) or re.fullmatch(r"[0-9.]+", server_name):
        raise ContractError("transport_server_name must be a DNS name")
    probe_url = urlparse(str(contract.get("private_probe_url", "")))
    try:
        probe_port = probe_url.port
    except ValueError:
        raise ContractError("private_probe_url has an invalid port") from None
    if (probe_url.scheme != "http" or probe_url.hostname != gateway_ip or probe_url.username
            or probe_url.password or probe_url.query or probe_url.fragment
            or probe_port is None or not 1024 <= probe_port <= 65535):
        raise ContractError("private_probe_url must be an exact high-port HTTP URL on gateway.overlay_ip")
    marker = contract.get("private_probe_marker")
    if not isinstance(marker, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", marker):
        raise ContractError("private_probe_marker is invalid")
    max_age = contract.get("max_handshake_age_seconds")
    if not isinstance(max_age, int) or not 1 <= max_age <= 300:
        raise ContractError("max_handshake_age_seconds must be from 1 through 300")

    known_hosts = runtime_path(environment, str(contract.get("known_hosts_file", "")), "known_hosts_file", exists=True, private=False)
    gateway_key = runtime_path(environment, str(gateway.get("private_key_file", "")), "gateway.private_key_file", exists=True, private=True)
    one_key = runtime_path(environment, str(one.get("private_key_file", "")), "one.private_key_file", exists=True, private=True)
    for node_target in (gateway_target, one_target):
        lookup = runner(["ssh-keygen", "-F", node_target, "-f", str(known_hosts)])
        if getattr(lookup, "returncode", 1) != 0 or not getattr(lookup, "stdout", "").strip():
            raise ContractError(f"reviewed known_hosts does not contain exact target {node_target}")

    variables = {
        "xconnect_evidence_environment": "uat",
        "xconnect_evidence_run_id": run_id,
        "xconnect_evidence_network_id": network_id,
        "xconnect_evidence_one_device_id": device_id,
        "xconnect_evidence_gateway_target": gateway_target,
        "xconnect_evidence_one_target": one_target,
        "xconnect_evidence_gateway_state_dir": gateway.get("state_dir", "/var/lib/xconnect-gateway"),
        "xconnect_evidence_one_state_dir": one.get("state_dir", "/var/lib/xconnect-one"),
        "xconnect_evidence_gateway_interface": gateway.get("wireguard_interface", "xconzero0"),
        "xconnect_evidence_one_interface": one.get("wireguard_interface", "xconone0"),
        "xconnect_evidence_gateway_overlay_ip": gateway_ip,
        "xconnect_evidence_transport_server_name": server_name,
        "xconnect_evidence_private_probe_url": probe_url.geturl(),
        "xconnect_evidence_private_probe_marker": marker,
        "xconnect_evidence_max_handshake_age_seconds": max_age,
        "xconnect_evidence_receipt_file": str(receipt_path),
    }
    for key in ("xconnect_evidence_gateway_state_dir", "xconnect_evidence_one_state_dir"):
        if not re.fullmatch(r"/[A-Za-z0-9._/-]+", str(variables[key])) or ".." in Path(str(variables[key])).parts:
            raise ContractError(f"{key} is invalid")
    for key in ("xconnect_evidence_gateway_interface", "xconnect_evidence_one_interface"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", str(variables[key])):
            raise ContractError(f"{key} is invalid")
    root = Path(__file__).resolve().parents[2]
    inventory_path = receipt_path.parent / f".{receipt_path.name}.inventory.json"
    variables_path = receipt_path.parent / f".{receipt_path.name}.variables.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    inventory_path.write_text(json.dumps({
        "xconnect_gateway": {"hosts": ["xconnect-gateway"]},
        "xconnect_one": {"hosts": ["xconnect-one"]},
        "_meta": {"hostvars": {
            "xconnect-gateway": {"ansible_host": gateway_target, "ansible_user": gateway["user"],
                                  "ansible_ssh_private_key_file": str(gateway_key)},
            "xconnect-one": {"ansible_host": one_target, "ansible_user": one["user"],
                              "ansible_ssh_private_key_file": str(one_key)},
        }},
    }, separators=(",", ":")), encoding="utf-8")
    inventory_path.chmod(0o600)
    variables_path.write_text(json.dumps(variables, separators=(",", ":")), encoding="utf-8")
    variables_path.chmod(0o600)
    child_env = dict(environment)
    child_env.update({
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_SSH_ARGS": f"-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}",
    })
    receipt_path.unlink(missing_ok=True)
    try:
        result = runner([
            "ansible-playbook", "-i", str(inventory_path), str(root / "xconnect-lab-evidence.yml"),
            "--extra-vars", f"@{variables_path}",
        ], env=child_env)
        if getattr(result, "returncode", 1) != 0:
            receipt_path.unlink(missing_ok=True)
            raise ContractError(f"XConnect data-plane verification failed (exit {getattr(result, 'returncode', 'unknown')})")
    finally:
        inventory_path.unlink(missing_ok=True)
        variables_path.unlink(missing_ok=True)
    if not receipt_path.is_file() or receipt_path.stat().st_mode & 0o077:
        raise ContractError("Playbooks did not produce a private evidence receipt")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt_path.unlink(missing_ok=True)
        raise ContractError("Playbooks produced an invalid evidence receipt") from None
    required_receipt = {
        "schema": "xconnect-lab-evidence/v1", "status": "verified", "environment": "uat",
        "run_id": run_id, "network_id": network_id, "one_device_id": device_id,
        "gateway_target": gateway_target, "one_target": one_target,
    }
    if any(receipt.get(key) != value for key, value in required_receipt.items()):
        receipt_path.unlink(missing_ok=True)
        raise ContractError("Playbooks evidence receipt does not match the reviewed contract")
    for proof in ("one_status_verified", "gateway_status_verified", "tls_sni_verified",
                  "private_ping_verified", "private_http_verified"):
        if receipt.get(proof) is not True:
            receipt_path.unlink(missing_ok=True)
            raise ContractError(f"Playbooks evidence receipt is missing {proof}")
    age = receipt.get("handshake_age_seconds")
    if not isinstance(age, int) or age < 0 or age > max_age:
        receipt_path.unlink(missing_ok=True)
        raise ContractError("Playbooks evidence receipt has a stale or invalid handshake")
    if environment.get("GITHUB_OUTPUT"):
        with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"run_id={run_id}\nnetwork_id={network_id}\nhandshake_age_seconds={age}\n")
    return receipt


def main() -> int:
    try:
        result = execute(dict(os.environ))
    except ContractError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(f"Verified XConnect data plane for {result['one_device_id']} in {result['run_id']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
