#!/usr/bin/env python3
"""Build the NodeDeployment entry for the existing Vault node and merge contracts.

The existing vault.svc.plus node is declared in the Vault service declaration
under ``spec.migration.source``. It joins the same contract as the new nodes
so one set of stages and checks covers both; it is reached with a short-lived
SSH certificate and, for Raft, only through its XConnect overlay address.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from render_inventory import validate

LEGACY_GROUPS = ["vault_legacy_source", "vault_shared_leader", "vault_single_node", "xconnect_one"]
LEGACY_STAGES = {
    # M4: the old node joins the shared XConnect network as a One node.
    "xconnect-one": ["xconnect_one"],
    "vault-legacy-convert": ["vault_legacy_source"],
    "vault-legacy-rollback": ["vault_legacy_source"],
    "vault-legacy-retire": ["vault_legacy_source"],
    "vault-single-raft": ["vault_single_node"],
}


def source_of(service: dict) -> dict:
    source = service.get("spec", {}).get("migration", {}).get("source")
    if not isinstance(source, dict):
        raise ValueError("spec.migration.source is not declared")
    return source


def legacy_contract(service: dict, name: str) -> dict:
    source = source_of(service)
    node = {
        "id": source["id"],
        "provider": "existing",
        "address": source["address"],
        "ssh_port": source.get("ssh_port", 22),
        "ssh_user": source["ssh_user"],
        "ssh_host_ed25519": source["ssh_host_ed25519"],
        "auth": {"adapter": "ssh-certificate", "principal": source["ssh_user"]},
        "groups": list(LEGACY_GROUPS),
    }
    overlay = source.get("overlay_address")
    if overlay:
        # Raft and the Vault API reach the old node only over XConnect.
        node["overlay_address"] = overlay
        node["private_address"] = overlay
    contract = {
        "apiVersion": "ops.svc.plus/v1alpha1",
        "kind": "NodeDeployment",
        "metadata": {"name": name},
        "spec": {
            "environment": service["metadata"]["environment"],
            "stages": list(LEGACY_STAGES),
            "stage_targets": LEGACY_STAGES,
            "nodes": [node],
        },
    }
    return validate(contract)


def merge(contracts: list[dict]) -> dict:
    if not contracts:
        raise ValueError("nothing to merge")
    documents = [validate(contract) for contract in contracts]
    environments = {document["spec"]["environment"] for document in documents}
    if len(environments) != 1:
        raise ValueError("contracts belong to different environments")
    modes = {document["spec"].get("connection", {}).get("mode") for document in documents} - {None}
    if len(modes) > 1:
        raise ValueError("contracts disagree about the connection mode")
    nodes: list[dict] = []
    stages: list[str] = []
    targets: dict[str, list[str]] = {}
    for document in documents:
        for node in document["spec"]["nodes"]:
            if any(existing["id"] == node["id"] for existing in nodes):
                raise ValueError(f"node {node['id']} appears in more than one contract")
            nodes.append(node)
        for stage in document["spec"]["stages"]:
            if stage not in stages:
                stages.append(stage)
            groups = targets.setdefault(stage, [])
            for group in document["spec"]["stage_targets"][stage]:
                if group not in groups:
                    groups.append(group)
    spec = {"environment": environments.pop(), "stages": stages, "stage_targets": targets, "nodes": nodes}
    if modes:
        spec["connection"] = {"mode": modes.pop()}
    merged = {
        "apiVersion": "ops.svc.plus/v1alpha1",
        "kind": "NodeDeployment",
        "metadata": {"name": documents[0]["metadata"]["name"]},
        "spec": spec,
    }
    return validate(merged)


def write(document: dict, output: Path) -> None:
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    output.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("contract")
    build.add_argument("--service-manifest", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    combine = commands.add_parser("merge")
    combine.add_argument("inputs", type=Path, nargs="+")
    combine.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "contract":
            service = yaml.safe_load(args.service_manifest.read_text(encoding="utf-8"))
            document = legacy_contract(service, service["metadata"]["name"])
        else:
            document = merge([json.loads(path.read_text(encoding="utf-8")) for path in args.inputs])
    except (KeyError, ValueError) as error:
        raise SystemExit(f"legacy source contract: {error}") from None
    write(document, args.output)
    print(f"wrote {len(document['spec']['nodes'])} node(s) to {args.output}")


if __name__ == "__main__":
    main()
