#!/usr/bin/env python3
"""Observe a Vault migration and recommend one reviewed controller stage.

This owner-side adapter performs the token-free SSH and DNS observations used
by ``migrate-auto``. It never expands the recommendation into a stage plan;
the Toolkit controller validates that recommendation against its reviewed
``stage_plan.py`` before any change or Vault login.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from render_inventory import validate
from verify_vault_stage import (
    LEGACY_GROUP,
    VAULT_UNIT,
    active,
    check_observation_window,
    dns_points_at_legacy,
    health_of,
    new_nodes,
    node_phase,
    probe,
    single,
    unsealed,
)


def blocked(message: str) -> dict[str, str]:
    return {"recommended_stage": "", "blocked": message}


def chosen(stage: str) -> dict[str, str]:
    return {"recommended_stage": stage, "blocked": ""}


def decide(
    contract: dict,
    probes: dict[str, dict],
    dns_on_legacy: bool,
    backup_declared: bool,
    observation: dict | None = None,
) -> dict[str, str]:
    """Return the same fail-closed recommendation as the frozen controller."""
    # Retained in the contract and evidence even though joining peers does not
    # require a disaster-recovery snapshot. The snapshot remains a separate,
    # explicitly selected stage.
    del backup_declared
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    source = probes[legacy["id"]]
    new = new_nodes(contract)
    if not source.get("reachable"):
        return blocked(f"{legacy['id']} is unreachable over the pinned SSH channel; fix access first.")

    source_running = source.get("units", {}).get(VAULT_UNIT) == "active"
    if not source_running and source.get("vault_enabled") == "disabled":
        if any(active(probes[node["id"]]) for node in new):
            return blocked(
                "Migration complete: the old node is retired and a new node leads. "
                "Now rekey, rotate and revoke the old root token by hand (M7), then remove spec.migration from GitOps."
            )
        return blocked(f"{legacy['id']} is retired but no new node is active; investigate before anything else.")
    if not health_of(source):
        return blocked(f"{legacy['id']}: Vault is not answering on its loopback listener; investigate.")

    storage = source.get("storage_type")
    if storage == "postgresql":
        if not unsealed(source):
            return blocked(f"{legacy['id']}: unseal the existing PostgreSQL-backed Vault first.")
        return chosen("migrate-convert")
    if storage != "raft":
        return blocked(f"{legacy['id']}: unsupported storage {storage!r}; stop and investigate.")
    if not unsealed(source):
        return blocked(f"{legacy['id']}: unseal the converted node with its existing key, then dispatch migrate-auto again.")

    phases = {node["id"]: node_phase(probes[node["id"]]) for node in new}
    sealed = sorted(node_id for node_id, phase in phases.items() if phase == "sealed")
    if sealed:
        return blocked(
            f"Unseal {', '.join(sealed)} with the existing key, confirm vault operator raft list-peers "
            "shows it as a voter, then dispatch migrate-auto again."
        )
    if any(phase == "empty" for phase in phases.values()):
        if not active(source):
            return blocked(f"{legacy['id']} is not the active node; new nodes can only join an active leader.")
        return chosen("migrate-join")

    if active(source):
        return chosen("migrate-cutover")
    if dns_on_legacy:
        return blocked(
            "Leadership has moved and the cluster is healthy. Switch the service DNS to the new entry point "
            "(the last traffic change), record spec.migration.observation.dns_switched_at in GitOps, "
            "then dispatch migrate-auto again."
        )
    try:
        check_observation_window(observation or {})
    except ValueError as waiting:
        return blocked(f"Observing the new entry point: {waiting}.")
    return chosen("migrate-remove")


def probe_fact(state: dict) -> dict:
    """Project only non-secret facts needed to review the recommendation."""
    health = health_of(state)
    return {
        "reachable": state.get("reachable") is True,
        "vault_unit": state.get("units", {}).get(VAULT_UNIT, ""),
        "vault_enabled": state.get("vault_enabled", ""),
        "storage_type": state.get("storage_type"),
        "initialized": health.get("initialized"),
        "sealed": health.get("sealed"),
        "standby": health.get("standby"),
        "cluster_id": health.get("cluster_id"),
        "phase": node_phase(state),
    }


def observe(
    contract: dict,
    key: Path,
    known_hosts: Path,
    vault_addr: str,
    backup_declared: bool,
    observation: dict,
    gateway_state: str = "",
) -> tuple[dict[str, str], dict]:
    probes = {
        node["id"]: probe(node, key, known_hosts, gateway_state)
        for node in contract["spec"]["nodes"]
    }
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    service_host = urlparse(vault_addr).hostname or ""
    dns_on_legacy = dns_points_at_legacy(service_host, legacy["address"])
    decision = decide(contract, probes, dns_on_legacy, backup_declared, observation)
    facts = {
        "schema": 1,
        "service_host": service_host,
        "dns_on_legacy": dns_on_legacy,
        "backup_declared": backup_declared,
        "nodes": {node_id: probe_fact(state) for node_id, state in probes.items()},
        **decision,
    }
    return decision, facts


def write_outputs(output: Path, decision: dict[str, str], facts: dict) -> None:
    values = {**decision, "facts": json.dumps(facts, separators=(",", ":"), sort_keys=True)}
    with output.open("a", encoding="utf-8") as stream:
        for name, value in values.items():
            stream.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--vault-addr", required=True)
    parser.add_argument("--backup-config", default="{}")
    parser.add_argument("--observation", default="{}", help="spec.migration.observation as JSON")
    parser.add_argument("--gateway-state", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    contract = validate(json.loads(args.contract.read_text(encoding="utf-8")))
    backup_config = json.loads(args.backup_config or "{}")
    observation = json.loads(args.observation or "{}")
    if not isinstance(backup_config, dict) or not isinstance(observation, dict):
        parser.error("backup config and observation must be JSON objects")
    decision, facts = observe(
        contract,
        args.key,
        args.known_hosts,
        args.vault_addr,
        bool(backup_config),
        observation,
        args.gateway_state,
    )
    if decision["recommended_stage"]:
        print(f"migrate-auto owner recommendation: {decision['recommended_stage']}")
    else:
        print(f"migrate-auto owner stopped: {decision['blocked']}")
    if args.github_output:
        write_outputs(args.github_output, decision, facts)
    print(json.dumps(facts, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
