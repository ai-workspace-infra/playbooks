#!/usr/bin/env python3
"""Build a private Ansible inventory for one reviewed non-IaC XConnect node.

The controller supplies a selected GitOps topology and an already-authorized
Vault response.  This adapter does not query Vault or create cloud/CMDB facts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from base64 import b64decode
from binascii import Error as Base64Error
from pathlib import Path

import yaml


EXPECTED_POOLS = {"jp", "us", "sg", "tw", "ph"}
DOMAIN_SUFFIX = {"uat": ".onwalk.net", "prod": ".svc.plus"}


def fail(message: str) -> None:
    raise ValueError(message)


def selected_node(topology: dict, environment: str, node_id: str) -> tuple[dict, dict, str]:
    topology_environment = str((topology.get("metadata") or {}).get("environment") or "").strip()
    if environment not in DOMAIN_SUFFIX:
        fail(f"environment must be uat or prod, got {environment!r}")
    if topology_environment != environment:
        fail("topology environment does not match the selected deployment environment")

    pools = (topology.get("spec") or {}).get("pools") or []
    if {pool.get("name") for pool in pools} != EXPECTED_POOLS:
        fail(f"topology must declare exactly these pools: {sorted(EXPECTED_POOLS)}")

    for pool in pools:
        for node in pool.get("nodes") or []:
            if node.get("id") != node_id:
                continue
            source = node.get("connection_source")
            if source not in (None, "vault"):
                fail(f"node {node_id!r} is not a Vault-backed non-IaC node")
            if source is None and pool.get("name") != "ph":
                fail("legacy topology without connection_source is accepted only for the PH pool")
            domain = str((pool.get("entrypoint") or {}).get("fqdn") or "").strip()
            expected = f"{pool.get('name')}-xconnect{DOMAIN_SUFFIX[environment]}"
            if domain != expected:
                fail(f"pool {pool.get('name')!r} must use {expected!r}")
            return pool, node, domain
    fail(f"non-IaC node {node_id!r} is not declared in the topology")


def node_secret(vault_response: dict, domain: str, node_id: str) -> dict:
    secret = ((vault_response.get("data") or {}).get("data") or {})
    candidates = (
        secret.get(domain),
        (secret.get("nodes") or {}).get(domain),
        secret.get(node_id),
        (secret.get("nodes") or {}).get(node_id),
        secret,
    )
    for candidate in candidates:
        # The first explicit record wins. An incomplete domain record must
        # fail rather than silently choosing another node/root connection.
        if isinstance(candidate, dict):
            return candidate
    fail(f"Vault record has no connection data for non-IaC node {node_id}")


def install_node_key(record: dict, destination: Path, node_id: str) -> None:
    encoded = record.get("ssh_private_key_b64") or record.get("SSH_PRIVATE_KEY_B64")
    if not encoded:
        return
    try:
        key = b64decode("".join(str(encoded).split()), validate=True)
    except (ValueError, Base64Error) as error:
        raise ValueError(f"Vault SSH private key is not valid base64 for {node_id}") from error
    if not key:
        fail(f"Vault SSH private key is empty for {node_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(key)
        os.chmod(temporary, 0o600)
        subprocess.run(
            ["ssh-keygen", "-y", "-f", str(temporary)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        os.replace(temporary, destination)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"Vault SSH private key is unusable for {node_id}") from error
    finally:
        temporary.unlink(missing_ok=True)


def render(args: argparse.Namespace) -> dict[str, str]:
    topology = yaml.safe_load(args.topology.read_text(encoding="utf-8")) or {}
    response = json.loads(args.vault_response.read_text(encoding="utf-8"))
    pool, selected, domain = selected_node(topology, args.environment, args.node_id)
    record = node_secret(response, domain, args.node_id)

    host = record.get("public_ipv4") or record.get("ip") or record.get("host") or selected.get("ansible_host")
    user = record.get("ansible_user") or record.get("user") or selected.get("ansible_user")
    password = record.get("SSH_PASSWORD") or record.get("password") or record.get("ansible_password")
    if not host or not user or not password:
        fail(f"Vault/GitOps connection data is incomplete for non-IaC node {args.node_id}")

    if args.deploy_key:
        install_node_key(record, args.deploy_key, args.node_id)
        if not args.deploy_key.is_file():
            fail("a reviewed fallback deploy key is required when the node record has no private key")
        os.chmod(args.deploy_key, 0o600)

    host_vars = {
        "ansible_host": host,
        "ansible_user": user,
        "ansible_password": password,
        "service_domains": [domain],
        "xconnect_region": pool.get("region", "ph-mnl"),
        "xconnect_pool": pool.get("name", "ph"),
        "xconnect_fqdn": domain,
        "xconnect_connection_source": "vault",
    }
    if args.deploy_key:
        host_vars["ansible_ssh_private_key_file"] = str(args.deploy_key)

    inventory = {
        "all": {
            "children": {
                "agent_proxy": {"hosts": {args.node_id: host_vars}},
                "xray_exporter": {"children": {"agent_proxy": {}}},
            }
        }
    }
    args.inventory.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.inventory.with_name(f".{args.inventory.name}.tmp")
    try:
        temporary.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, args.inventory)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "inventory": str(args.inventory),
        "deploy_key_file": str(args.deploy_key or ""),
        "domain": domain,
        "region": str(pool.get("region", "ph-mnl")),
        "pool": str(pool.get("name", "ph")),
    }


def write_outputs(outputs: dict[str, str], output_file: Path | None) -> None:
    if output_file is None:
        return
    with output_file.open("a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--vault-response", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--deploy-key", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        outputs = render(args)
        write_outputs(outputs, args.github_output)
        print(f"Rendered private non-IaC inventory for {args.node_id} ({outputs['region']})")
        return 0
    except (ValueError, OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        print(f"non-IaC inventory adapter refused input: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
