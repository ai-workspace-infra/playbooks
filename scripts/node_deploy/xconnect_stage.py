#!/usr/bin/env python3
"""Control-plane side of the XConnect node stages.

The playbooks own everything that changes a host (install, `init`, `join`,
services). This script owns what only the control plane may do:

* read non-secret topology from GitOps (network, controller, Gateway id,
  transport host, release tags);
* issue a one-use, device-bound Zero invitation for a node that is not
  enrolled yet, using the Gateway's WireGuard public key that the
  `*-identity` tag created; an enrolled node never gets (or consumes) one;
* hand the playbook its variables as JSON extra-vars.

    xconnect_stage.py arch --role gateway|one --contract C --key K --known-hosts H
    xconnect_stage.py vars --role gateway|one --topology T --contract C \
        --artifacts DIR --secrets-dir DIR [--invite --key K --known-hosts H]

`one` covers the new peers and, while spec.migration is declared, the
existing vault.svc.plus node (M4): every node in the xconnect_one group that
has not joined gets its own invitation.

    xconnect_stage.py operator-invite --topology T --contract C --key K \
        --known-hosts H --gateway-state S --vault-path kv/data/...

issues a one-use invitation for the operator device declared in GitOps (C5)
and writes it straight to Vault (VAULT_ADDR / VAULT_TOKEN, a create/update
grant only); the operator reads it with their own Vault login. It never
touches disk or logs.

Secrets come from the environment (ZERO_SERVICE_TOKEN, ZERO_OWNER_EMAIL,
XCONNECT_VLESS_ID) and never appear in the output; invitation join URIs are
written only to 0600 files in the runner-private --secrets-dir.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from render_inventory import validate
from verify_vault_stage import GATEWAY_GROUP, gateway_status, members, probe, single

ONE_GROUP = "xconnect_one"
ROLES = ("gateway", "one")
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
JOIN_URI = re.compile(r"^xconnect://join/\S+$")
INVITE_MINUTES = 30
BOOTSTRAP_PATH = "/api/internal/overlay/networks/bootstrap"
HTTP_USER_AGENT = "platform-ops-toolkit/1.0 (+https://github.com/ai-workspace-infra/platform-ops-toolkit)"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
ROLE_GROUPS = {"gateway": GATEWAY_GROUP, "one": ONE_GROUP}


def load_topology(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc.get("kind") != "XConnectOneNodeSet":
        raise ValueError("XConnect topology must be an XConnectOneNodeSet")
    spec = doc["spec"]
    network, transport = spec["network"], spec["network"]["transport_profile"]
    dns = spec.get("dns", {}) or {}
    dns_enabled = bool(dns.get("enabled", False))
    dns_listen_address = str(dns.get("listen_address", network["gateway_wireguard_address"].split("/", 1)[0]))
    dns_interface = str(dns.get("interface", spec["runtime"].get("wireguard_interface", "xconone0")))
    dns_zone = str(dns.get("zone", ""))
    dns_domains = [str(domain) for domain in dns.get("domains", [])]
    dns_upstream_servers = [str(server) for server in dns.get("upstream_servers", [])]
    if dns_enabled:
        try:
            if not isinstance(ipaddress.ip_address(dns_listen_address), ipaddress.IPv4Address):
                raise ValueError
        except ValueError:
            raise ValueError("XConnect overlay DNS listen_address must be an IP address") from None
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", dns_interface):
            raise ValueError("XConnect overlay DNS interface is invalid")
        if not HOSTNAME.fullmatch(dns_zone):
            raise ValueError("XConnect overlay DNS zone must be a valid name")
        if not dns_upstream_servers:
            raise ValueError("enabled XConnect overlay DNS requires upstream_servers")
        for server in dns_upstream_servers:
            try:
                if not isinstance(ipaddress.ip_address(server), ipaddress.IPv4Address):
                    raise ValueError
            except ValueError:
                raise ValueError("XConnect overlay DNS upstream_servers require IPv4 addresses") from None
        if not isinstance(dns.get("records", []), list) or not dns.get("records", []):
            raise ValueError("enabled XConnect overlay DNS requires records")
        seen_dns_names = set()
        for record in dns["records"]:
            if not isinstance(record, dict) or not HOSTNAME.fullmatch(str(record.get("name", ""))):
                raise ValueError("XConnect overlay DNS records require valid FQDN names")
            if record["name"] in seen_dns_names:
                raise ValueError("XConnect overlay DNS record names must be unique")
            seen_dns_names.add(record["name"])
            if set(record) != {"name", "device_id"} or not IDENTIFIER.fullmatch(str(record.get("device_id", ""))):
                raise ValueError("XConnect overlay DNS aliases must map an FQDN to a valid device_id")
    topology = {
        "environment": doc["metadata"]["environment"],
        "network_id": network["id"],
        "cidr": network["cidr"],
        "gateway_address": network["gateway_wireguard_address"],
        "gateway_id": spec["gateway"]["id"],
        "controller": spec["control_plane"]["accounts_api_url"],
        "transport_host": transport["host"],
        "transport_port": int(transport.get("port", 443)),
        "transport_path": transport.get("path", "/xconnect"),
        "transport_mode": transport.get("mode", "auto"),
        "transport_kind": transport.get("kind", "vless-xhttp"),
        "frontend": transport.get("frontend", "direct-tls"),
        "listen_socket": transport.get("listen_socket", ""),
        "gateway_state_dir": spec["runtime"]["gateway_state_dir"],
        "one_state_dir": f"{spec['runtime']['state_dir_prefix']}/{doc['metadata']['environment']}",
        "wireguard_interface": spec["runtime"].get("wireguard_interface", "xconone0"),
        "xray_loopback_port": int(spec["runtime"].get("xray_loopback_udp_port", 51830)),
        "sync_interval": int(spec["runtime"].get("sync_interval_seconds", 300)),
        "dns_enabled": dns_enabled,
        "dns_interface": dns_interface,
        "dns_listen_address": dns_listen_address,
        "dns_zone": dns_zone,
        "dns_domains": dns_domains,
        "dns_upstream_servers": dns_upstream_servers,
        "dns_records": dns.get("records", []),
    }
    if not topology["controller"].startswith("https://"):
        raise ValueError("XConnect controller must be an https URL")
    if not HOSTNAME.fullmatch(topology["transport_host"]):
        raise ValueError("invalid XConnect transport host")
    for key in ("network_id", "gateway_id"):
        if not IDENTIFIER.fullmatch(topology[key]):
            raise ValueError(f"invalid XConnect {key}")
    return topology


def expires_at(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)) + timedelta(minutes=INVITE_MINUTES)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def bootstrap_request(topology: dict, role: str, device_id: str, gateway_key: str, owner: str, vless_id: str,
                      expires: str, platform: str = "linux") -> dict:
    return {
        "owner_email": owner,
        "bootstrap": {
            "controller_url": topology["controller"],
            "network": {
                "id": topology["network_id"],
                "display_name": f"XConnect {topology['environment']} Vault network",
                "cidr": topology["cidr"],
                "gateway_id": topology["gateway_id"],
                "gateway_wireguard_public_key": gateway_key,
                "gateway_wireguard_address": topology["gateway_address"],
                "gateway_endpoint_host": topology["transport_host"],
                "gateway_endpoint_port": 51820,
                "transport_server_name": topology["transport_host"],
                "transport_port": topology["transport_port"],
                "transport_auth_id": vless_id,
                "transport_kind": topology["transport_kind"],
                "transport_path": topology["transport_path"],
                "transport_mode": topology["transport_mode"],
                "transport_host": topology["transport_host"],
                # The Gateway's Xray frontend belongs to this network, not the
                # whole Zero deployment: a Caddy-fronted Gateway shares :443.
                "gateway_frontend": topology["frontend"],
                "gateway_listen_socket": topology["listen_socket"],
            },
            "invite": {"device_id": device_id, "platform": platform, "role": role, "expires_at": expires},
        },
    }


def check_invite(response: dict, topology: dict, role: str, device_id: str, platform: str = "linux") -> str:
    """Fail closed unless the invitation is bound to exactly this device."""
    invite = response.get("invite") or {}
    bound = (
        (response.get("network") or {}).get("id") == topology["network_id"]
        and invite.get("network_id") == topology["network_id"]
        and invite.get("device_id") == device_id
        and invite.get("role") == role
        and invite.get("platform") == platform
        and invite.get("remaining_uses") == 1
    )
    if not bound:
        raise ValueError(f"Zero returned an invitation that is not bound to {role} {device_id}")
    join_uri = str(response.get("join_uri") or "")
    if not JOIN_URI.fullmatch(join_uri):
        raise ValueError("Zero returned no usable join URI")
    return join_uri


def post_json(url: str, token: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"X-Service-Token": token, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": HTTP_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Return a fixed diagnostic, never the raw error body (which can echo
        # request credentials). Distinguish an edge rejection from owner ACLs.
        raw = error.read(4096)
        diagnostic = "http_error"
        if b"error code: 1010" in raw:
            diagnostic = "cloudflare_browser_integrity_rejection"
        else:
            try:
                value = json.loads(raw)
                code = value.get("error") if isinstance(value, dict) else None
                if code in {"forbidden", "owner_not_found", "not_found", "invalid_request", "device_conflict"}:
                    diagnostic = code
            except (ValueError, UnicodeError):
                pass
        return error.code, {"diagnostic": diagnostic}


def request_join_uri(topology: dict, role: str, device_id: str, gateway_key: str, env: dict[str, str],
                     post=post_json, platform: str = "linux", expires: str | None = None) -> str:
    token, owner, vless_id = (env.get(name, "") for name in ("ZERO_SERVICE_TOKEN", "ZERO_OWNER_EMAIL", "XCONNECT_VLESS_ID"))
    if not (token and owner and vless_id):
        raise ValueError("ZERO_SERVICE_TOKEN, ZERO_OWNER_EMAIL and XCONNECT_VLESS_ID are required to issue an invitation")
    body = bootstrap_request(topology, role, device_id, gateway_key, owner, vless_id, expires or expires_at(), platform)
    status, response = post(topology["controller"].rstrip("/") + BOOTSTRAP_PATH, token, body)
    if status != 201:
        diagnostic = response.get("diagnostic", "http_error")
        raise ValueError(f"Zero did not issue the {role} invitation for {device_id}: HTTP {status} ({diagnostic})")
    return check_invite(response, topology, role, device_id, platform)


def issue_invite(topology: dict, role: str, device_id: str, gateway_key: str, secrets_dir: Path,
                 env: dict[str, str], post=post_json) -> Path:
    join_uri = request_join_uri(topology, role, device_id, gateway_key, env, post)
    secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = secrets_dir / f"{device_id}.invite"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(join_uri + "\n")
    return path


def gateway_vars(topology: dict, contract: dict, artifacts: Path, secrets_dir: Path, invite: Path | None) -> dict:
    gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
    if gateway["id"] != topology["gateway_id"]:
        raise ValueError(f"contract Gateway {gateway['id']} is not the topology Gateway {topology['gateway_id']}")
    return {
        "xconnect_gateway_enabled": True,
        "xconnect_gateway_environment": topology["environment"],
        "xconnect_gateway_state_dir": topology["gateway_state_dir"],
        "xconnect_gateway_controller": topology["controller"],
        "xconnect_gateway_id": topology["gateway_id"],
        "xconnect_gateway_binary_source": str(artifacts / "xconnect-gateway"),
        "xconnect_gateway_xray_binary_source": str(artifacts / "xray"),
        "xconnect_gateway_frontend": topology["frontend"],
        "xconnect_gateway_listen_socket": topology["listen_socket"],
        "xconnect_gateway_trust_bundle_source": str(secrets_dir / "trust-bundle.pem"),
        "xconnect_gateway_sync_interval_seconds": topology["sync_interval"],
        "xconnect_gateway_invite_file_source": str(invite) if invite else "",
        "xconnect_gateway_dns_enabled": topology["dns_enabled"],
        "xconnect_gateway_dns_interface": topology["dns_interface"],
        "xconnect_gateway_dns_listen_address": topology["dns_listen_address"],
        "xconnect_gateway_dns_zone": topology["dns_zone"],
        "xconnect_gateway_dns_domains": topology["dns_domains"],
        "xconnect_gateway_dns_upstream_servers": topology["dns_upstream_servers"],
        "xconnect_gateway_dns_records": topology["dns_records"],
    }


def architecture(nodes: list[dict], probes: dict[str, dict]) -> str:
    """One release architecture for every target node, from live `uname -m`."""
    found = set()
    for node in nodes:
        machine = str(probes[node["id"]].get("machine") or "")
        if machine not in ARCHITECTURES:
            raise ValueError(f"{node['id']}: unsupported or unknown CPU architecture {machine!r}")
        found.add(ARCHITECTURES[machine])
    if len(found) != 1:
        raise ValueError(f"target nodes disagree on CPU architecture: {sorted(found)}")
    return found.pop()


def one_vars(topology: dict, artifacts: Path, secrets_dir: Path, invites: dict[str, Path]) -> dict:
    # Extra-vars are templated per host, so one set of vars serves every node:
    # each host picks its own invitation (if any) by inventory name == node id.
    return {
        "xconnect_one_enabled": True,
        "xconnect_one_environment": topology["environment"],
        "xconnect_one_state_dir": topology["one_state_dir"],
        "xconnect_one_binary_source": str(artifacts / "xconnect"),
        "xconnect_one_ca_certificate_source": str(secrets_dir / "trust-bundle.pem"),
        "xconnect_one_device_id": "{{ inventory_hostname }}",
        "xconnect_one_device_name": "{{ inventory_hostname }}",
        "xconnect_one_expected_network_id": topology["network_id"],
        "xconnect_one_expected_overlay_cidr": topology["cidr"],
        "xconnect_one_expected_wireguard_interface": topology["wireguard_interface"],
        "xconnect_one_expected_xray_loopback_port": topology["xray_loopback_port"],
        "xconnect_one_sync_interval_seconds": topology["sync_interval"],
        "xconnect_one_dns_enabled": topology["dns_enabled"],
        "xconnect_one_dns_server": topology["dns_listen_address"],
        "xconnect_one_dns_domains": topology["dns_domains"],
        # Host metrics come from node-process-metrics; keep One to the overlay.
        "xconnect_one_install_observability": False,
        # The migration source may already be a One on another network; every
        # One binds the same loopback port, so only it leaves that network first.
        "xconnect_one_release_foreign_overlays": "{{ 'vault_legacy_source' in group_names }}",
        "xconnect_one_invite_files": {node_id: str(path) for node_id, path in sorted(invites.items())},
        "xconnect_one_invite_file_source": "{{ (xconnect_one_invite_files | default({}))[inventory_hostname] | default('') }}",
    }


def enrolled_gateway_key(contract: dict, key: Path, known_hosts: Path, gateway_state: str) -> str:
    gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
    status = gateway_status(probe(gateway, key, known_hosts, gateway_state))
    if not (status["enrolled"] and status["public_key"]):
        raise ValueError(f"{gateway['id']}: enroll the XConnect Gateway (xconnect-gateway) before any One")
    return status["public_key"]


def one_invites(topology: dict, contract: dict, key: Path, known_hosts: Path, gateway_state: str,
                secrets_dir: Path) -> dict[str, Path]:
    gateway_key = enrolled_gateway_key(contract, key, known_hosts, gateway_state)
    state_file = f"{topology['one_state_dir']}/state.json"
    invites = {}
    for node in members(contract, ONE_GROUP):
        # The probe's state-file reader is generic: for One, a present
        # state.json is what the role itself treats as joined.
        if gateway_status(probe(node, key, known_hosts, state_file))["exists"]:
            print(f"{node['id']} has already joined; no invitation issued", file=sys.stderr)
            continue
        invites[node["id"]] = issue_invite(topology, "one", node["id"], gateway_key, secrets_dir, dict(os.environ))
    return invites


OPERATOR_DEVICE = re.compile(r"^xconnect-(darwin|linux|windows)-[a-z0-9][a-z0-9._-]{0,100}$")
VAULT_KV_PATH = re.compile(r"^kv/data/[A-Za-z0-9/_-]+$")


def operator_device(doc: dict) -> tuple[str, str]:
    """The single operator device declared in GitOps, and its platform."""
    devices = doc["spec"].get("operator_devices") or []
    if len(devices) != 1:
        raise ValueError("exactly one operator device must be declared in the XConnect topology")
    device_id = str(devices[0].get("id", ""))
    match = OPERATOR_DEVICE.fullmatch(device_id)
    if not match or devices[0].get("enrollment") != "short-lived-single-use-invite":
        raise ValueError("the operator device must be xconnect-<platform>-<name> with a single-use invite enrollment")
    return device_id, match.group(1)


def vault_write(vault_addr: str, token: str, path: str, data: dict) -> int:
    request = urllib.request.Request(
        f"{vault_addr.rstrip('/')}/v1/{path}",
        data=json.dumps({"data": data}).encode(),
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def operator_invite(topology: dict, doc: dict, gateway_key: str, vault_path: str, env: dict[str, str],
                    post=post_json, write=vault_write) -> dict:
    if not VAULT_KV_PATH.fullmatch(vault_path):
        raise ValueError("invalid Vault KV path for the operator invitation")
    if not (env.get("VAULT_ADDR", "").startswith("https://") and env.get("VAULT_TOKEN")):
        raise ValueError("VAULT_ADDR (https) and VAULT_TOKEN are required to store the operator invitation")
    device_id, platform = operator_device(doc)
    expires = expires_at()
    join_uri = request_join_uri(topology, "one", device_id, gateway_key, env, post, platform, expires)
    status = write(env["VAULT_ADDR"], env["VAULT_TOKEN"], vault_path,
                   {"join_uri": join_uri, "device_id": device_id, "expires_at": expires})
    if status not in (200, 204):
        raise ValueError(f"could not store the operator invitation in Vault: HTTP {status}")
    return {"device_id": device_id, "platform": platform, "expires_at": expires, "vault_path": vault_path}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["arch", "vars", "operator-invite"])
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--topology", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--secrets-dir", type=Path)
    parser.add_argument("--invite", action="store_true", help="issue an invitation if the node is not enrolled")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--gateway-state", default="")
    parser.add_argument("--vault-path", default="")
    args = parser.parse_args()
    if args.command != "operator-invite" and not args.role:
        parser.error("--role is required")

    contract = validate(json.loads(args.contract.read_text(encoding="utf-8")))
    if args.command == "operator-invite":
        if not (args.topology and args.key and args.known_hosts):
            raise SystemExit("::error::operator-invite needs --topology, --key and --known-hosts")
        try:
            gateway_key = enrolled_gateway_key(contract, args.key, args.known_hosts, args.gateway_state)
            doc = yaml.safe_load(args.topology.read_text(encoding="utf-8"))
            result = operator_invite(load_topology(args.topology), doc, gateway_key, args.vault_path, dict(os.environ))
        except ValueError as error:
            raise SystemExit(f"::error::{error}") from None
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == "arch":
        if not (args.key and args.known_hosts):
            raise SystemExit("::error::arch needs --key and --known-hosts")
        nodes = members(contract, ROLE_GROUPS[args.role])
        probes = {node["id"]: probe(node, args.key, args.known_hosts, "") for node in nodes}
        try:
            print(architecture(nodes, probes))
        except ValueError as error:
            raise SystemExit(f"::error::{error}") from None
        return
    if not (args.topology and args.artifacts and args.secrets_dir):
        raise SystemExit("::error::vars needs --topology, --artifacts and --secrets-dir")
    topology = load_topology(args.topology)
    if args.role == "one":
        invites = {}
        if args.invite:
            if not (args.key and args.known_hosts):
                raise SystemExit("::error::--invite needs --key and --known-hosts to read live node state")
            try:
                invites = one_invites(topology, contract, args.key, args.known_hosts, args.gateway_state, args.secrets_dir)
            except ValueError as error:
                raise SystemExit(f"::error::{error}") from None
        print(json.dumps(one_vars(topology, args.artifacts, args.secrets_dir, invites), sort_keys=True))
        return
    invite = None
    if args.invite:
        if not (args.key and args.known_hosts):
            raise SystemExit("::error::--invite needs --key and --known-hosts to read live Gateway state")
        gateway = single(contract, GATEWAY_GROUP, "XConnect Gateway")
        status = gateway_status(probe(gateway, args.key, args.known_hosts, args.gateway_state))
        if status["enrolled"]:
            print(f"{gateway['id']} is already enrolled; no invitation issued", file=sys.stderr)
        elif not status["public_key"]:
            raise SystemExit(f"::error::{gateway['id']} has no WireGuard identity; run xconnect-gateway-identity first")
        else:
            invite = issue_invite(topology, "gateway", gateway["id"], status["public_key"], args.secrets_dir, dict(os.environ))
    print(json.dumps(gateway_vars(topology, contract, args.artifacts, args.secrets_dir, invite), sort_keys=True))


if __name__ == "__main__":
    main()
