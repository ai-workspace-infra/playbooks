#!/usr/bin/env python3
"""Probe live nodes and enforce the manual Vault checkpoints between stages.

The probe reads only unauthenticated, non-secret state over the pinned SSH
channel: sudo availability, swap, free disk, Vault's loopback ``sys/health``,
``sys/leader`` and ``sys/seal-status`` endpoints, listening sockets, the
Vault port guard, service unit states, and whether the other nodes' Raft
ports (8200/8201) are reachable from the node. It never needs a Vault token, so
GitHub Actions can gate stages without holding root tokens or unseal shares.
Operators still confirm ``vault operator raft list-peers``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from render_inventory import validate
CHECKS = {
    "access", "no-foreign-cluster", "leader-running", "leader-unsealed",
    "peers-running", "raft-quorum", "raft-quorum-new", "monitoring-running",
    "gateway-enrolled", "gateway-identity", "gateway-running", "legacy-unsealed",
    "legacy-report", "legacy-overlay", "legacy-raft", "legacy-standby",
    "vault-port-guard", "next-peer", "selected-running", "overlay-raft-path",
    "raft-overlay", "service-dns-moved", "service-endpoint", "observation-window",
}

LEADER_GROUP = "vault_shared_leader"
PEER_GROUP = "vault_shared_peers"
GATEWAY_GROUP = "xconnect_gateway"
LEGACY_GROUP = "vault_legacy_source"
VAULT_UNIT = "vault"
MONITORING_UNITS = ("node-exporter", "process-exporter", "vector")
GATEWAY_UNITS = ("xconnect-gateway-xray", "xconnect-gateway-sync.timer")
VAULT_PORTS = (8200, 8201)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
GUARD_TABLE = "vault_port_guard"
INIT_FILE = "/etc/vault.d/vault_init.json"
MIN_FREE_MB = 1024
SAFE_ARGUMENT = re.compile(r"^[A-Za-z0-9_./-]*$")
SAFE_TARGETS = re.compile(r"^([0-9a-fA-F.:]+:[0-9]{1,5}(,[0-9a-fA-F.:]+:[0-9]{1,5})*)?$")
RAFT_PORTS = (8200, 8201)
DEFAULT_OBSERVE_HOURS = 24

REMOTE_PROBE = r"""
import json, os, socket, subprocess, sys, urllib.error, urllib.request

def api(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8200" + path, timeout=8) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)
        except ValueError:
            return {"errors": ["HTTP %d" % error.code]}
    except (OSError, ValueError):
        return None

def run(*argv):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)

swap_kb = 0
with open("/proc/swaps") as swaps:
    for line in swaps.readlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[2].isdigit():
            swap_kb += int(fields[2])
units, gateway_state, init_file, guard_table, targets = sys.argv[1:6]
listeners = []
for line in run("ss", "-Hltn").stdout.splitlines():
    fields = line.split()
    if len(fields) >= 4:
        address, _, port = fields[3].rpartition(":")
        if port in ("8200", "8201"):
            listeners.append({"address": address.strip("[]"), "port": int(port)})
def gateway(path):
    # Report only whether the Gateway enrolled and its public WireGuard key;
    # the device credential itself never leaves the node.
    if not path:
        return {"exists": False, "enrolled": False, "public_key": ""}
    result = run("sudo", "-n", "cat", path)
    if result.returncode != 0:
        return {"exists": False, "enrolled": False, "public_key": ""}
    try:
        state = json.loads(result.stdout)
    except ValueError:
        return {"exists": True, "enrolled": False, "public_key": ""}
    credential = (state.get("device_credential") or {}).get("credential") or ""
    return {"exists": True, "enrolled": bool(credential), "public_key": str(state.get("wireguard_public_key") or "")}

def reach(target):
    # open: something listens; refused: the packet arrived (nothing listens
    # yet); blocked: dropped on the way (overlay down or policy denies it).
    host, _, port = target.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=4).close()
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except (OSError, ValueError):
        return "blocked"

def overlay():
    # IPv4 addresses on XConnect/WireGuard interfaces: the node's live overlay IPs.
    found = []
    for line in run("ip", "-4", "-o", "addr", "show").stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1].startswith(("xcon", "wg")):
            found.append({"interface": fields[1], "address": fields[3].split("/")[0]})
    return found

disk = os.statvfs("/")
seal = api("/v1/sys/seal-status") or {}
print(json.dumps({
    "sudo": run("sudo", "-n", "true").returncode == 0,
    "machine": os.uname().machine,
    "swap_kb": swap_kb,
    "free_mb": disk.f_bavail * disk.f_frsize // 1048576,
    "health": api("/v1/sys/health?standbycode=200&sealedcode=200&uninitcode=200"),
    "leader": api("/v1/sys/leader"),
    "storage_type": seal.get("storage_type"),
    "version": seal.get("version"),
    "units": {unit: run("systemctl", "is-active", unit).stdout.strip() for unit in units.split(",") if unit},
    "vault_enabled": run("systemctl", "is-enabled", "vault").stdout.strip(),
    "gateway": gateway(gateway_state),
    "init_file": bool(init_file) and run("sudo", "-n", "test", "-e", init_file).returncode == 0,
    "port_guard": run("sudo", "-n", "nft", "list", "table", "inet", guard_table).returncode == 0,
    "listeners": listeners,
    "reach": {target: reach(target) for target in targets.split(",") if target},
    "overlay": overlay(),
}))
"""


def probe(node: dict, key: Path, known_hosts: Path, gateway_state: str, targets: str = "") -> dict:
    units = ",".join((VAULT_UNIT, *MONITORING_UNITS, *GATEWAY_UNITS))
    init_file = INIT_FILE if LEGACY_GROUP in node.get("groups", []) else ""
    for value in (gateway_state, init_file):
        if not SAFE_ARGUMENT.fullmatch(value):
            raise ValueError("probe path contains unsupported characters")
    if not SAFE_TARGETS.fullmatch(targets):
        raise ValueError("probe targets must be comma-separated address:port pairs")
    command = [
        "ssh", "-i", str(key),
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=12",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "HostKeyAlgorithms=ssh-ed25519",
        "-p", str(node.get("ssh_port", 22)),
        f"{node['ssh_user']}@{node['address']}",
        "python3", "-",
        *(shlex.quote(value) for value in (units, gateway_state, init_file, GUARD_TABLE, targets)),
    ]
    try:
        result = subprocess.run(
            # Each unreachable Raft target may take its full 4 s connect timeout.
            command, input=REMOTE_PROBE, capture_output=True, text=True,
            timeout=40 + 5 * len([target for target in targets.split(",") if target]), check=False,
        )
    except subprocess.TimeoutExpired:
        return {"reachable": False}
    if result.returncode != 0:
        return {"reachable": False}
    try:
        state = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"reachable": False}
    if not isinstance(state, dict):
        return {"reachable": False}
    state["reachable"] = True
    return state


def members(contract: dict, group: str) -> list[dict]:
    return [node for node in contract["spec"]["nodes"] if group in node.get("groups", [])]


def new_nodes(contract: dict) -> list[dict]:
    return [node for node in contract["spec"]["nodes"] if LEGACY_GROUP not in node.get("groups", [])]


def health_of(state: dict) -> dict:
    health = state.get("health")
    return health if isinstance(health, dict) and "initialized" in health else {}


def unsealed(state: dict) -> bool:
    health = health_of(state)
    return health.get("initialized") is True and health.get("sealed") is False


def active(state: dict) -> bool:
    return unsealed(state) and health_of(state).get("standby") is False


def single(contract: dict, group: str, label: str) -> dict:
    nodes = members(contract, group)
    if len(nodes) != 1:
        raise ValueError(f"exactly one declared {label} is required")
    return nodes[0]


def check_access(nodes: list[dict], probes: dict[str, dict]) -> None:
    for node in nodes:
        state = probes[node["id"]]
        if not state.get("reachable"):
            raise ValueError(f"{node['id']}: SSH probe failed over the pinned host key")
        if state.get("sudo") is not True:
            raise ValueError(f"{node['id']}: non-interactive sudo is unavailable for the short-lived login")
        # Nodes this pipeline builds must run without swap. The migration
        # source is an existing node being retired: its swap is reported in
        # the node table, not made a precondition for moving off it.
        if state.get("swap_kb") != 0 and LEGACY_GROUP not in node.get("groups", []):
            raise ValueError(f"{node['id']}: swap is enabled; Vault Raft nodes must run without swap")


def check_no_foreign_cluster(contract: dict, probes: dict[str, dict]) -> None:
    clusters = {
        health_of(probes[node["id"]]).get("cluster_id")
        for node in contract["spec"]["nodes"]
        if unsealed(probes[node["id"]])
    }
    clusters.discard(None)
    if len(clusters) > 1:
        raise ValueError("unsealed nodes report different Vault cluster IDs; stop and investigate")
    leaders = members(contract, LEADER_GROUP)
    leader_initialized = bool(leaders) and health_of(probes[leaders[0]["id"]]).get("initialized") is True
    for node in members(contract, PEER_GROUP):
        if unsealed(probes[node["id"]]) and not leader_initialized:
            raise ValueError(f"{node['id']}: peer is unsealed while the leader is uninitialized")


def check_running(nodes: list[dict], probes: dict[str, dict]) -> None:
    for node in nodes:
        state = probes[node["id"]]
        if not health_of(state):
            raise ValueError(f"{node['id']}: Vault is not answering on its loopback listener")
        if state.get("units", {}).get(VAULT_UNIT) != "active":
            raise ValueError(f"{node['id']}: the {VAULT_UNIT} service is not active")


def check_leader_unsealed(contract: dict, probes: dict[str, dict]) -> None:
    leader = single(contract, LEADER_GROUP, "Vault leader")
    if not unsealed(probes[leader["id"]]):
        raise ValueError(
            f"{leader['id']}: Vault leader must be initialized and unsealed by an operator before peers"
        )
    check_no_foreign_cluster(contract, probes)


def check_raft_quorum(contract: dict, probes: dict[str, dict]) -> None:
    nodes = contract["spec"]["nodes"]
    addresses = {node.get("private_address") for node in nodes} | {node.get("overlay_address") for node in nodes}
    clusters: set[str] = set()
    leader_addresses: set[str] = set()
    active_nodes = 0
    for node in nodes:
        state = probes[node["id"]]
        health = health_of(state)
        if not unsealed(state):
            raise ValueError(f"{node['id']}: Vault must be manually unsealed before this stage")
        if state.get("storage_type") not in (None, "raft"):
            raise ValueError(f"{node['id']}: storage is {state.get('storage_type')}, not Raft")
        cluster_id = health.get("cluster_id")
        if not isinstance(cluster_id, str) or not cluster_id:
            raise ValueError(f"{node['id']}: Vault cluster identity is missing")
        clusters.add(cluster_id)
        if health.get("standby") is False:
            active_nodes += 1
        leader = state.get("leader") if isinstance(state.get("leader"), dict) else {}
        address = leader.get("leader_cluster_address")
        if not isinstance(address, str) or not address:
            raise ValueError(f"{node['id']}: no Raft leader is visible from this node")
        leader_addresses.add(address)
    if len(clusters) != 1:
        raise ValueError("Vault nodes report different cluster IDs; they are not one Raft cluster")
    if active_nodes != 1:
        raise ValueError(f"Vault HA needs exactly one active node; found {active_nodes}")
    if len(leader_addresses) != 1:
        raise ValueError("Vault nodes disagree about the Raft leader")
    host = urlparse(next(iter(leader_addresses))).hostname
    if host not in addresses:
        raise ValueError("the Raft leader is not a declared node's private or overlay address")


def check_monitoring(nodes: list[dict], probes: dict[str, dict]) -> None:
    for node in nodes:
        units = probes[node["id"]].get("units", {})
        stopped = [unit for unit in MONITORING_UNITS if units.get(unit) != "active"]
        if stopped:
            raise ValueError(f"{node['id']}: monitoring units are not active: {', '.join(stopped)}")


WIREGUARD_KEY = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def gateway_status(probe_state: dict) -> dict:
    status = probe_state.get("gateway") or {}
    return {
        "exists": status.get("exists") is True,
        "enrolled": status.get("enrolled") is True,
        "public_key": status.get("public_key") if WIREGUARD_KEY.fullmatch(str(status.get("public_key") or "")) else "",
    }


def check_gateway_enrolled(contract: dict, probes: dict[str, dict]) -> None:
    # state.json exists right after `init`; only a stored credential is enrollment.
    gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
    if not gateway_status(probes[gateway["id"]])["enrolled"]:
        raise ValueError(f"{gateway['id']}: XConnect Gateway is not enrolled with Zero")


def check_gateway_identity(contract: dict, probes: dict[str, dict]) -> None:
    gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
    if not gateway_status(probes[gateway["id"]])["public_key"]:
        raise ValueError(f"{gateway['id']}: XConnect Gateway has no local WireGuard identity yet")


def check_gateway_running(contract: dict, probes: dict[str, dict]) -> None:
    check_gateway_enrolled(contract, probes)
    gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
    units = probes[gateway["id"]].get("units", {})
    stopped = [unit for unit in GATEWAY_UNITS if units.get(unit) != "active"]
    if stopped:
        raise ValueError(f"{gateway['id']}: XConnect Gateway units are not active: {', '.join(stopped)}")


def check_legacy_unsealed(contract: dict, probes: dict[str, dict]) -> None:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    if not unsealed(probes[legacy["id"]]):
        raise ValueError(f"{legacy['id']}: the existing Vault must be initialized and unsealed")


def check_legacy_report(contract: dict, probes: dict[str, dict]) -> list[str]:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    state = probes[legacy["id"]]
    free_mb = state.get("free_mb")
    if not isinstance(free_mb, int) or free_mb < MIN_FREE_MB:
        raise ValueError(f"{legacy['id']}: less than {MIN_FREE_MB} MiB free for the Raft data and backup")
    warnings = []
    if state.get("init_file"):
        warnings.append(
            f"{legacy['id']}: {INIT_FILE} (unseal key and root token) is on disk; "
            "rekey and revoke the root token after the migration"
        )
    storage = state.get("storage_type")
    if storage not in ("postgresql", "raft"):
        raise ValueError(f"{legacy['id']}: unsupported source storage {storage!r}")
    return warnings


def check_legacy_overlay(contract: dict) -> None:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    overlay = legacy.get("overlay_address")
    if not overlay or legacy.get("private_address") != overlay:
        raise ValueError(
            f"{legacy['id']}: declare its XConnect overlay address first; Raft must never use a public address"
        )


def check_raft_overlay(contract: dict) -> None:
    """Every node's Raft address is its recorded XConnect overlay IP (Raft over the overlay)."""
    pending = [
        node["id"] for node in contract["spec"]["nodes"]
        if not node.get("overlay_address") or node.get("private_address") != node.get("overlay_address")
    ]
    if pending:
        raise ValueError(
            f"{', '.join(pending)}: no XConnect overlay IP recorded for Raft yet; enroll with xconnect-gateway / "
            "xconnect-one and record the assigned IPs in GitOps first"
        )


def check_legacy_raft(contract: dict, probes: dict[str, dict]) -> None:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    state = probes[legacy["id"]]
    if state.get("storage_type") != "raft":
        raise ValueError(f"{legacy['id']}: convert the existing Vault to Raft (migrate-convert) first")
    if not active(state):
        raise ValueError(f"{legacy['id']}: the existing Vault must be unsealed and active before new nodes join")
    leader = state.get("leader") if isinstance(state.get("leader"), dict) else {}
    cluster_address = leader.get("leader_cluster_address")
    parsed = urlparse(cluster_address) if isinstance(cluster_address, str) else None
    if not parsed or parsed.scheme != "https" or parsed.hostname != legacy.get("overlay_address") or parsed.port != 8201:
        raise ValueError(
            f"{legacy['id']}: Vault advertises Raft cluster address {cluster_address!r}, "
            f"not the declared XConnect overlay {legacy.get('overlay_address')}:8201; "
            "do not join a peer until the source advertises its reachable overlay address"
        )


def check_legacy_standby(contract: dict, probes: dict[str, dict]) -> None:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    if not unsealed(probes[legacy["id"]]) or health_of(probes[legacy["id"]]).get("standby") is not True:
        raise ValueError(f"{legacy['id']}: the old node is still active; leadership did not move")
    if not any(active(probes[node["id"]]) for node in new_nodes(contract)):
        raise ValueError("no new node is active after the leadership transfer")


def check_port_guard(contract: dict, probes: dict[str, dict]) -> None:
    for node in members(contract, LEGACY_GROUP):
        state = probes[node["id"]]
        allowed = LOOPBACK | {node.get("overlay_address")}
        exposed = [
            listener for listener in state.get("listeners", [])
            if listener.get("address") not in allowed
        ]
        if exposed and state.get("port_guard") is not True:
            raise ValueError(
                f"{node['id']}: Vault listens beyond loopback and overlay without the {GUARD_TABLE} firewall table"
            )


def node_phase(state: dict) -> str:
    """empty (nothing installed or joined), sealed (joined, waiting for a key), unsealed."""
    health = health_of(state)
    if not health or health.get("initialized") is not True:
        return "empty"
    return "unsealed" if health.get("sealed") is False else "sealed"


def next_peer(contract: dict, probes: dict[str, dict]) -> dict:
    """The one peer to install or join next; the previous one must be unsealed first.

    Peers join one at a time so an operator unseals and checks each node
    (vault operator raft list-peers) before the next one exists.
    """
    peers = members(contract, PEER_GROUP)
    waiting = [node["id"] for node in peers if node_phase(probes[node["id"]]) == "sealed"]
    if waiting:
        raise ValueError(
            f"{', '.join(waiting)} joined but is still sealed: unseal it and confirm "
            "vault operator raft list-peers shows it as a voter before the next node joins"
        )
    empty = [node for node in peers if node_phase(probes[node["id"]]) == "empty"]
    if not empty:
        raise ValueError("every declared peer has already joined; nothing to install")
    return empty[0]


def check_selected_running(contract: dict, probes: dict[str, dict], selected: list[str]) -> None:
    if not selected:
        raise ValueError("no node was selected for this one-node stage")
    nodes = [node for node in contract["spec"]["nodes"] if node["id"] in selected]
    check_running(nodes, probes)
    for node in nodes:
        if node_phase(probes[node["id"]]) == "empty":
            raise ValueError(f"{node['id']}: Vault is running but has not joined the Raft cluster")


def raft_targets(contract: dict, node: dict) -> str:
    """Raft addresses this node must reach: every other node's private (Raft) address."""
    targets = []
    for other in contract["spec"]["nodes"]:
        address = other.get("private_address")
        if other["id"] != node["id"] and address:
            targets.extend(f"{address}:{port}" for port in RAFT_PORTS)
    return ",".join(targets)


def check_overlay_raft_path(contract: dict, probes: dict[str, dict]) -> None:
    """Every node reaches every other node's Raft address; the old node must answer.

    "refused" still proves the path (nothing listens yet on a node that has not
    joined); "blocked" means the overlay or its access policy drops the packets.
    """
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    for node in contract["spec"]["nodes"]:
        reach = probes[node["id"]].get("reach") or {}
        for other in contract["spec"]["nodes"]:
            address = other.get("private_address")
            if other["id"] == node["id"] or not address:
                continue
            for port in RAFT_PORTS:
                result = reach.get(f"{address}:{port}")
                if result not in ("open", "refused"):
                    raise ValueError(
                        f"{node['id']} cannot reach {other['id']} at {address}:{port} over the overlay; "
                        "check XConnect and the access policy for tcp 8200/8201"
                    )
                if other["id"] == legacy["id"] and result != "open":
                    raise ValueError(f"{legacy['id']} does not answer on {address}:{port} from {node['id']}")


def addresses(host: str) -> set[str]:
    try:
        return {item[4][0] for item in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return set()


def dns_points_at_legacy(service_host: str, legacy_address: str) -> bool:
    """Conservative: an unresolvable service name counts as still on the old node."""
    service = addresses(service_host)
    return not service or bool(service & addresses(legacy_address))


def check_service_dns_moved(contract: dict, vault_addr: str) -> None:
    legacy = single(contract, LEGACY_GROUP, "legacy source node")
    host = urlparse(vault_addr).hostname or ""
    if dns_points_at_legacy(host, legacy["address"]):
        raise ValueError(f"{host} still resolves to {legacy['id']}; switch the service DNS to the new entry point first")


def service_health(vault_addr: str) -> dict:
    url = vault_addr.rstrip("/") + "/v1/sys/health?standbycode=200&perfstandbyok=true"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)
        except ValueError:
            return {}
    except (OSError, ValueError):
        return {}


def check_service_endpoint(contract: dict, probes: dict[str, dict], vault_addr: str) -> None:
    """The public service address answers, unsealed, as the declared nodes' cluster."""
    if not vault_addr.startswith("https://"):
        raise ValueError("the service address must be https")
    health = service_health(vault_addr)
    if health.get("initialized") is not True or health.get("sealed") is not False:
        raise ValueError(f"{vault_addr} is not answering as an initialized, unsealed Vault")
    clusters = {health_of(probes[node["id"]]).get("cluster_id") for node in new_nodes(contract)}
    if health.get("cluster_id") not in clusters - {None}:
        raise ValueError(f"{vault_addr} answers for a different Vault cluster than the declared nodes")


def check_observation_window(observation: dict, now: datetime | None = None) -> None:
    """The old peer stays until the new entry point has served for the declared window."""
    switched = str(observation.get("dns_switched_at") or "")
    if not switched:
        raise ValueError(
            "declare spec.migration.observation.dns_switched_at (UTC) in GitOps when the service DNS moves; "
            "the old peer is kept for the observation window after that"
        )
    try:
        moment = datetime.fromisoformat(switched.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("spec.migration.observation.dns_switched_at is not an ISO 8601 time") from None
    if moment.tzinfo is None:
        raise ValueError("spec.migration.observation.dns_switched_at must carry a UTC offset")
    hours = observation.get("hours", DEFAULT_OBSERVE_HOURS)
    if not isinstance(hours, int) or hours < 1:
        raise ValueError("spec.migration.observation.hours must be a positive integer")
    ends = moment + timedelta(hours=hours)
    current = now or datetime.now(timezone.utc)
    if current < ends:
        raise ValueError(
            f"observation window runs until {ends.isoformat()}; keep the old peer until then "
            "(roll back by pointing the service DNS at the old node)"
        )


def verify(
    contract: dict,
    checks: list[str],
    probes: dict[str, dict],
    selected: list[str] | None = None,
    vault_addr: str = "",
    observation: dict | None = None,
) -> list[str]:
    unknown = set(checks) - CHECKS
    if unknown:
        raise ValueError(f"unknown checks: {sorted(unknown)}")
    nodes = contract["spec"]["nodes"]
    warnings: list[str] = []
    for check in checks:
        if check == "access":
            check_access(nodes, probes)
        elif check == "no-foreign-cluster":
            check_no_foreign_cluster(contract, probes)
        elif check == "leader-running":
            check_running([single(contract, LEADER_GROUP, "Vault leader")], probes)
        elif check == "leader-unsealed":
            check_leader_unsealed(contract, probes)
        elif check == "peers-running":
            check_running(members(contract, PEER_GROUP), probes)
        elif check == "raft-quorum":
            check_raft_quorum(contract, probes)
        elif check == "raft-quorum-new":
            remaining = {**contract, "spec": {**contract["spec"], "nodes": new_nodes(contract)}}
            check_raft_quorum(remaining, probes)
        elif check == "monitoring-running":
            check_monitoring(new_nodes(contract), probes)
        elif check == "gateway-enrolled":
            check_gateway_enrolled(contract, probes)
        elif check == "gateway-identity":
            check_gateway_identity(contract, probes)
        elif check == "gateway-running":
            check_gateway_running(contract, probes)
        elif check == "legacy-unsealed":
            check_legacy_unsealed(contract, probes)
        elif check == "legacy-report":
            warnings.extend(check_legacy_report(contract, probes))
        elif check == "legacy-overlay":
            check_legacy_overlay(contract)
        elif check == "legacy-raft":
            check_legacy_raft(contract, probes)
        elif check == "raft-overlay":
            check_raft_overlay(contract)
        elif check == "legacy-standby":
            check_legacy_standby(contract, probes)
        elif check == "vault-port-guard":
            check_port_guard(contract, probes)
        elif check == "next-peer":
            next_peer(contract, probes)
        elif check == "selected-running":
            check_selected_running(contract, probes, selected or [])
        elif check == "overlay-raft-path":
            check_overlay_raft_path(contract, probes)
        elif check == "service-dns-moved":
            check_service_dns_moved(contract, vault_addr)
        elif check == "service-endpoint":
            check_service_endpoint(contract, probes, vault_addr)
        elif check == "observation-window":
            check_observation_window(observation or {})
    return warnings


def describe(state: dict) -> str:
    if not state.get("reachable"):
        return "unreachable"
    health = health_of(state)
    if not health:
        return "not running"
    if health.get("initialized") is not True:
        return "uninitialized"
    if health.get("sealed") is not False:
        return "sealed"
    return "active" if health.get("standby") is False else "standby"


def xconnect_state(node: dict, state: dict) -> str:
    """Gateway enrollment, or the overlay IPs live on the node's XConnect interfaces."""
    if not state.get("reachable"):
        return "-"
    parts = []
    if GATEWAY_GROUP in node.get("groups", []):
        gateway = gateway_status(state)
        parts.append("gateway enrolled" if gateway["enrolled"] else "gateway identity" if gateway["public_key"] else "gateway not set up")
    addresses = [item.get("address") for item in state.get("overlay") or [] if item.get("address")]
    parts.append(f"overlay {', '.join(addresses)}" if addresses else "no overlay IP")
    return "; ".join(parts)


def summary(contract: dict, probes: dict[str, dict], title: str) -> str:
    lines = [
        f"### {title}",
        "",
        "| Node | Address | Raft address | SSH user | XConnect | Vault | Storage | Version | Cluster | sudo | swap | node-exporter | process-exporter | vector |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for node in contract["spec"]["nodes"]:
        state = probes[node["id"]]
        cluster = str(health_of(state).get("cluster_id") or "")[:8] or "-"
        units = state.get("units", {})
        lines.append(
            f"| {node['id']} | {node.get('address', '-')} | {node.get('private_address') or '-'} | "
            f"{node.get('ssh_user', '-')} | {xconnect_state(node, state)} | "
            f"{describe(state)} | {state.get('storage_type') or '-'} | "
            f"{state.get('version') or '-'} | {cluster} | {state.get('sudo', '-')} | "
            f"{state.get('swap_kb', '-')} | "
            + " | ".join(units.get(unit, "-") or "-" for unit in MONITORING_UNITS)
            + " |"
        )
    return "\n".join(lines) + "\n"


def probe_all(contract: dict, key: Path, known_hosts: Path, gateway_state: str, with_paths: bool) -> dict[str, dict]:
    return {
        node["id"]: probe(
            node, key, known_hosts, gateway_state, raft_targets(contract, node) if with_paths else ""
        )
        for node in contract["spec"]["nodes"]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--checks", default="", help="comma-separated checks; empty only reports state")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--gateway-state", default="")
    parser.add_argument("--title", default="Vault node state")
    parser.add_argument("--selected", default="", help="comma-separated node ids a one-node stage changed")
    parser.add_argument("--vault-addr", default="", help="service address for service-* checks")
    parser.add_argument("--observation", default="{}", help="spec.migration.observation as JSON")
    parser.add_argument(
        "--select-next-peer", type=Path, metavar="GITHUB_OUTPUT",
        help="after the checks pass, write node=<the next peer to install or join>",
    )
    args = parser.parse_args()
    contract = validate(json.loads(args.contract.read_text(encoding="utf-8")))
    checks = [check for check in args.checks.split(",") if check]
    selected = [node for node in args.selected.split(",") if node]
    observation = json.loads(args.observation or "{}")
    error: ValueError | None = None
    warnings: list[str] = []
    chosen: dict | None = None
    for attempt in range(max(args.attempts, 1)):
        if attempt:
            time.sleep(args.interval)
        probes = probe_all(contract, args.key, args.known_hosts, args.gateway_state, "overlay-raft-path" in checks)
        try:
            warnings = verify(contract, checks, probes, selected, args.vault_addr, observation)
            if args.select_next_peer:
                chosen = next_peer(contract, probes)
            error = None
            break
        except ValueError as failure:
            error = failure
    report = summary(contract, probes, args.title)
    report += "".join(f"\n> ⚠️ {warning}\n" for warning in warnings)
    if chosen is not None:
        report += f"\nNext node for this stage: **{chosen['id']}** (one node per dispatch)\n"
    print(report)
    for warning in warnings:
        print(f"::warning::{warning}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as stream:
            stream.write(report + "\n")
    if error is not None:
        raise SystemExit(f"::error::{error}")
    if chosen is not None and args.select_next_peer:
        with args.select_next_peer.open("a", encoding="utf-8") as stream:
            stream.write(f"node={chosen['id']}\n")
    print(f"Verified: {', '.join(checks) or 'state report only'}")


if __name__ == "__main__":
    main()
