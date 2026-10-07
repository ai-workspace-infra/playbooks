#!/usr/bin/env python3
"""Run an XConnect host Role against one exact, pre-trusted UAT target."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable


OPERATIONS = {
    "gateway_identity", "gateway", "gateway_reconcile", "gateway_verify",
    "one", "one_verify",
}


class ContractError(RuntimeError):
    pass


def required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ContractError(f"{name} is required")
    return value


def target(value: str) -> str:
    if any(mark in value for mark in ("*", "?", "[", "]", ",", "/", " ")):
        raise ContractError("XCONNECT_RUNTIME_TARGET must be one exact host")
    try:
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
            raise ContractError("XCONNECT_RUNTIME_TARGET is not routable")
        return value
    except ValueError:
        pass
    if (value.lower().rstrip(".") == "localhost" or len(value) > 253 or not re.fullmatch(
            r"(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value)):
        raise ContractError("XCONNECT_RUNTIME_TARGET must be one exact IPv4 or DNS host")
    return value.rstrip(".")


def runtime_path(environment: dict[str, str], name: str, *, exists: bool, private: bool) -> Path:
    root = Path(required(environment, "RUNNER_TEMP")).resolve(strict=True)
    candidate = Path(required(environment, name))
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ContractError(f"{name} must be an absolute non-symlink path below RUNNER_TEMP")
    try:
        resolved = candidate.resolve(strict=exists)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ContractError(f"{name} must remain below RUNNER_TEMP") from None
    if exists and not resolved.is_file():
        raise ContractError(f"{name} must be a regular file")
    if exists and private and resolved.stat().st_mode & 0o077:
        raise ContractError(f"{name} must not be accessible by group or other")
    return resolved


def load_variables(path: Path) -> dict:
    try:
        variables = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ContractError("XCONNECT_RUNTIME_VARIABLES_FILE must contain a JSON object") from None
    if not isinstance(variables, dict):
        raise ContractError("XCONNECT_RUNTIME_VARIABLES_FILE must contain a JSON object")
    forbidden = {name for name in variables if re.search(
        r"^(?:ansible_|terraform)|cloudflare|aws_(?:access|secret|session)|vault_(?:token|addr)", name, re.I)}
    if forbidden:
        raise ContractError("host Role variables contain a forbidden cloud or control-plane key")
    return variables


def checked_run(command, **kwargs):
    return subprocess.run(command, check=False, text=True, capture_output=True, **kwargs)


def current_run(environment: dict[str, str]) -> str:
    run_id = required(environment, "GITHUB_RUN_ID")
    attempt = required(environment, "GITHUB_RUN_ATTEMPT")
    if not run_id.isdigit() or not attempt.isdigit() or int(run_id) < 1 or int(attempt) < 1:
        raise ContractError("GitHub run identity is invalid")
    return f"xcl-{run_id}-{attempt}"


def owner_sha(environment: dict[str, str], root: Path, runner: Callable[..., object]) -> str:
    expected = required(environment, "XCONNECT_RUNTIME_OWNER_SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ContractError("XCONNECT_RUNTIME_OWNER_SHA must be a full commit SHA")
    action_ref = environment.get("XCONNECT_RUNTIME_ACTION_REF", "").strip()
    if action_ref:
        if action_ref != expected:
            raise ContractError("pinned action ref does not match XCONNECT_RUNTIME_OWNER_SHA")
        return expected
    resolved = runner(["git", "-C", str(root), "rev-parse", "HEAD"])
    if getattr(resolved, "returncode", 1) != 0 or getattr(resolved, "stdout", "").strip() != expected:
        raise ContractError("checked-out Playbooks SHA does not match XCONNECT_RUNTIME_OWNER_SHA")
    return expected


def execute(
    environment: dict[str, str],
    runner: Callable[..., object] = checked_run,
) -> dict[str, str]:
    receipt_path = runtime_path(environment, "XCONNECT_RUNTIME_RECEIPT_FILE", exists=False, private=False)
    receipt_path.unlink(missing_ok=True)
    operation = required(environment, "XCONNECT_RUNTIME_OPERATION")
    if operation not in OPERATIONS:
        raise ContractError("XCONNECT_RUNTIME_OPERATION is not an approved host operation")
    exact_target = target(required(environment, "XCONNECT_RUNTIME_TARGET"))
    ssh_user = required(environment, "XCONNECT_RUNTIME_SSH_USER")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", ssh_user):
        raise ContractError("XCONNECT_RUNTIME_SSH_USER is invalid")
    key = runtime_path(environment, "XCONNECT_RUNTIME_PRIVATE_KEY_FILE", exists=True, private=True)
    known_hosts = runtime_path(environment, "XCONNECT_RUNTIME_KNOWN_HOSTS_FILE", exists=True, private=False)
    variables_path = runtime_path(environment, "XCONNECT_RUNTIME_VARIABLES_FILE", exists=True, private=True)
    variables = load_variables(variables_path)

    root = Path(__file__).resolve().parents[2]
    bound_run = current_run(environment)
    bound_owner = owner_sha(environment, root, runner)

    lookup = runner(["ssh-keygen", "-F", exact_target, "-f", str(known_hosts)])
    if getattr(lookup, "returncode", 1) != 0 or not getattr(lookup, "stdout", "").strip():
        raise ContractError("reviewed known_hosts does not contain the exact target")
    matching_keys = [line.split() for line in getattr(lookup, "stdout", "").splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
    if not matching_keys or len(matching_keys[0]) < 3:
        raise ContractError("reviewed known_hosts has no SHA256 host key fingerprint")
    try:
        key_blob = base64.b64decode(matching_keys[0][2], validate=True)
    except (ValueError, TypeError):
        raise ContractError("reviewed known_hosts has an invalid host key") from None
    encoded_fingerprint = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    fingerprint = f"SHA256:{encoded_fingerprint}"

    playbook = root / "xconnect-lab-runtime.yml"
    inventory = receipt_path.parent / f".{receipt_path.name}.inventory.json"
    merged = receipt_path.parent / f".{receipt_path.name}.variables.json"
    inventory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    inventory.write_text(json.dumps({
        "xconnect_target": {"hosts": ["xconnect-owner-target"]},
        "_meta": {"hostvars": {"xconnect-owner-target": {
            "ansible_host": exact_target,
            "ansible_user": ssh_user,
            "ansible_ssh_private_key_file": str(key),
        }}},
    }, separators=(",", ":")), encoding="utf-8")
    inventory.chmod(0o600)
    variables.update({
        "xconnect_lab_runtime_hosts": "xconnect_target",
        "xconnect_lab_runtime_target": exact_target,
        "xconnect_lab_runtime_environment": "uat",
        "xconnect_lab_runtime_operation": operation,
    })
    merged.write_text(json.dumps(variables, separators=(",", ":")), encoding="utf-8")
    merged.chmod(0o600)
    child_env = dict(environment)
    child_env.update({
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_SSH_ARGS": (
            "-o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts}"
        ),
    })
    try:
        result = runner([
            "ansible-playbook", "-i", str(inventory), str(playbook),
            "--limit", "xconnect-owner-target", "--extra-vars", f"@{merged}",
        ], env=child_env)
        if getattr(result, "returncode", 1) != 0:
            raise ContractError(f"Playbooks host operation failed (exit {getattr(result, 'returncode', 'unknown')})")
    except Exception:
        receipt_path.unlink(missing_ok=True)
        raise
    finally:
        inventory.unlink(missing_ok=True)
        merged.unlink(missing_ok=True)
    receipt = {
        "schema": "xconnect-lab-runtime-owner/v1",
        "environment": "uat",
        "run_id": bound_run,
        "owner_sha": bound_owner,
        "operation": operation,
        "target": exact_target,
        "host_key_fingerprint": fingerprint,
        "status": "completed",
    }
    receipt_path.write_text(json.dumps(receipt, separators=(",", ":")), encoding="utf-8")
    receipt_path.chmod(0o600)
    if environment.get("GITHUB_OUTPUT"):
        with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(
                f"run_id={bound_run}\nowner_sha={bound_owner}\noperation={operation}\n"
                f"target={exact_target}\nhost_key_fingerprint={fingerprint}\n"
            )
    return receipt


def main() -> int:
    try:
        result = execute(dict(os.environ))
    except ContractError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(f"Playbooks completed {result['operation']} for exact UAT target {result['target']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
