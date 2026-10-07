#!/usr/bin/env python3
"""Move Raft leadership off the existing Vault node and remove it, safely.

Runs on the CI runner against the Vault API with a short-lived token from a
role limited to reading the Raft configuration, stepping down and removing
peers. Every action is restricted to the node declared as the migration
source and only proceeds when all declared new nodes are Raft voters.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def request(method: str, path: str, body: dict | None = None) -> dict:
    address = os.environ["VAULT_ADDR"].rstrip("/")
    token = os.environ["VAULT_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    call = urllib.request.Request(
        f"{address}/v1/{path}",
        data=data,
        method=method,
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"::error::Vault {method} {path} failed with HTTP {error.code}") from None
    return json.loads(payload) if payload else {}


def servers() -> list[dict]:
    return request("GET", "sys/storage/raft/configuration")["data"]["config"]["servers"]


def leader_of(config: list[dict]) -> str | None:
    return next((server["node_id"] for server in config if server.get("leader")), None)


def require_voters(config: list[dict], expected: list[str]) -> None:
    voters = {server["node_id"] for server in config if server.get("voter")}
    missing = sorted(set(expected) - voters)
    if missing:
        raise ValueError(f"new nodes are not Raft voters yet: {', '.join(missing)}")


def plan_step_down(config: list[dict], legacy: str, expected: list[str]) -> bool:
    """Return True when a step-down is needed, False when leadership already moved."""
    require_voters(config, expected)
    if legacy not in {server["node_id"] for server in config}:
        raise ValueError(f"{legacy} is not a Raft member; nothing to hand over")
    leader = leader_of(config)
    if leader in expected:
        return False
    if leader != legacy:
        raise ValueError(f"unexpected Raft leader {leader!r}; stop and investigate")
    return True


def plan_remove(config: list[dict], legacy: str, expected: list[str]) -> bool:
    """Return True when the legacy peer must be removed, False when it is already gone."""
    require_voters(config, expected)
    members = {server["node_id"] for server in config}
    if legacy not in members:
        return False
    if leader_of(config) not in expected:
        raise ValueError("move leadership to a new node (migrate-cutover) before removing the old one")
    unexpected = members - set(expected) - {legacy}
    if unexpected:
        raise ValueError(f"undeclared Raft members present: {', '.join(sorted(unexpected))}")
    return True


def report(config: list[dict]) -> None:
    for server in config:
        role = "leader" if server.get("leader") else ("voter" if server.get("voter") else "non-voter")
        print(f"{server['node_id']:<28} {role:<10} {server.get('address', '')}")


def step_down(legacy: str, expected: list[str], attempts: int, wait: int) -> None:
    for attempt in range(attempts):
        config = servers()
        report(config)
        if not plan_step_down(config, legacy, expected):
            print(f"Raft leader is {leader_of(config)}; {legacy} is no longer leading")
            return
        print(f"asking {legacy} to step down (attempt {attempt + 1}/{attempts})")
        request("PUT", "sys/step-down")
        time.sleep(wait)
    raise SystemExit(f"::error::{legacy} kept winning the Raft election after {attempts} step-downs")


def remove_peer(legacy: str, expected: list[str]) -> None:
    config = servers()
    report(config)
    if not plan_remove(config, legacy, expected):
        print(f"{legacy} is not a Raft member")
        return
    request("POST", "sys/storage/raft/remove-peer", {"server_id": legacy})
    config = servers()
    report(config)
    if legacy in {server["node_id"] for server in config}:
        raise SystemExit(f"::error::{legacy} is still a Raft member")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "step-down", "remove-peer"])
    parser.add_argument("--legacy-id", required=True)
    parser.add_argument("--expect", nargs="+", required=True, help="declared new node ids")
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--wait", type=int, default=15)
    args = parser.parse_args()
    if args.legacy_id in args.expect:
        raise SystemExit("::error::the legacy node cannot also be a declared new node")
    try:
        if args.command == "status":
            report(servers())
        elif args.command == "step-down":
            step_down(args.legacy_id, args.expect, args.attempts, args.wait)
        else:
            remove_peer(args.legacy_id, args.expect)
    except ValueError as error:
        raise SystemExit(f"::error::{error}") from None


if __name__ == "__main__":
    main()
