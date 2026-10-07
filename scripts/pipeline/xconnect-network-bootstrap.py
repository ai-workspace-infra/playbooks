#!/usr/bin/env python3
"""Issue a reviewed XConnect Zero Gateway invitation through Accounts.

This is a Playbooks service-operation owner. It receives an already validated
private request and a runtime service credential from the Toolkit caller. It
does not authenticate to Vault, persist secrets, run Terraform, or touch hosts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ContractError(RuntimeError):
    pass


def required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ContractError(f"{name} is required")
    return value


def private_path(environment: dict[str, str], name: str, *, must_exist: bool) -> Path:
    root = Path(required(environment, "RUNNER_TEMP")).resolve(strict=True)
    value = Path(required(environment, name))
    if not value.is_absolute() or value.is_symlink():
        raise ContractError(f"{name} must be an absolute non-symlink path below RUNNER_TEMP")
    try:
        resolved = value.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ContractError(f"{name} must remain below RUNNER_TEMP") from None
    if must_exist and (not resolved.is_file() or resolved.stat().st_mode & 0o077):
        raise ContractError(f"{name} must be a private regular file")
    return resolved


def accounts_origin(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    approved = host in {"svc.plus", "onwalk.net"} or host.endswith((".svc.plus", ".onwalk.net"))
    if (parsed.scheme != "https" or not approved or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ContractError("ACCOUNTS_API_URL must be an approved HTTPS service origin")
    return value.rstrip("/")


def load_request(path: Path, network_id: str, accounts_url: str) -> tuple[dict, str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        bootstrap = value["bootstrap"]
        network = bootstrap["network"]
        invitation = bootstrap["invite"]
        gateway_id = network["gateway_id"]
        device_id = invitation["device_id"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ContractError("validated network request has an invalid shape") from None
    if value.get("owner_email", "").strip() == "":
        raise ContractError("validated network request has no owner")
    if bootstrap.get("controller_url", "").rstrip("/") != accounts_url:
        raise ContractError("validated network request Accounts target mismatch")
    if network.get("id") != network_id:
        raise ContractError("validated network request identity mismatch")
    if invitation.get("role") != "gateway" or invitation.get("platform") != "linux":
        raise ContractError("validated network request invitation scope mismatch")
    if not isinstance(gateway_id, str) or not re.fullmatch(r"gw-[A-Za-z0-9][A-Za-z0-9_-]{1,62}", gateway_id):
        raise ContractError("validated network request gateway identity is invalid")
    if not isinstance(device_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", device_id):
        raise ContractError("validated network request device identity is invalid")
    return value, gateway_id, device_id


def send_request(url: str, token: str, body: bytes) -> tuple[int, object]:
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"X-Service-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, None
    except (URLError, TimeoutError, ValueError):
        raise ContractError("Accounts service request failed") from None


def execute(
    environment: dict[str, str],
    sender: Callable[[str, str, bytes], tuple[int, object]] = send_request,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, str]:
    network_id = required(environment, "NETWORK_ID")
    if not re.fullmatch(r"net_[A-Za-z0-9][A-Za-z0-9_-]{1,62}", network_id):
        raise ContractError("NETWORK_ID is invalid")
    try:
        ttl = int(required(environment, "INVITATION_TTL_MINUTES"))
    except ValueError:
        raise ContractError("INVITATION_TTL_MINUTES must be between 5 and 30") from None
    if not 5 <= ttl <= 30:
        raise ContractError("INVITATION_TTL_MINUTES must be between 5 and 30")
    accounts_url = accounts_origin(required(environment, "ACCOUNTS_API_URL"))
    token = required(environment, "ZERO_SERVICE_TOKEN")
    request_path = private_path(environment, "NETWORK_REQUEST_FILE", must_exist=True)
    response_path = private_path(environment, "NETWORK_RESPONSE_FILE", must_exist=False)
    request, gateway_id, device_id = load_request(request_path, network_id, accounts_url)
    expires = now().astimezone(timezone.utc) + timedelta(minutes=ttl)
    request["bootstrap"]["invite"]["expires_at"] = expires.isoformat(timespec="seconds").replace("+00:00", "Z")
    request["bootstrap"]["invite"].pop("ttl_minutes", None)
    status, response = sender(
        accounts_url + "/api/internal/overlay/networks/bootstrap",
        token,
        json.dumps(request, separators=(",", ":")).encode("utf-8"),
    )
    if status != 201:
        raise ContractError(f"Accounts service rejected network bootstrap (HTTP {status})")
    if not isinstance(response, dict):
        raise ContractError("Accounts service returned an invalid bootstrap response")
    invite = response.get("invite")
    join_uri = response.get("join_uri")
    if (not isinstance(invite, dict) or response.get("network", {}).get("id") != network_id
            or invite.get("network_id") != network_id or invite.get("device_id") != device_id
            or invite.get("role") != "gateway" or invite.get("platform") != "linux"
            or invite.get("remaining_uses") != 1 or not isinstance(join_uri, str)
            or not join_uri.startswith("xconnect://join/")):
        raise ContractError("Accounts service response does not match the requested invitation")
    handoff = {"network_id": network_id, "gateway_id": gateway_id, "join_uri": join_uri}
    response_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    response_path.write_text(json.dumps(handoff, separators=(",", ":")), encoding="utf-8")
    response_path.chmod(0o600)
    if environment.get("GITHUB_OUTPUT"):
        with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"network_id={network_id}\ngateway_id={gateway_id}\n")
    return handoff


def main() -> int:
    try:
        result = execute(dict(os.environ))
    except ContractError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(f"Accounts service issued a one-use Gateway invitation for {result['network_id']}; private handoff ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
