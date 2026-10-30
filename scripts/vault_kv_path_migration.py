#!/usr/bin/env python3
"""Copy explicitly reviewed Vault KV v2 fields between paths without exposing values."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    pass


@dataclass
class VaultClient:
    address: str
    token: str
    namespace: str = ""
    timeout: int = 15

    def _request(self, method: str, mount: str, path: str, payload: dict | None = None) -> tuple[int, dict | None]:
        address = self.address.rstrip("/")
        api_path = "/v1/" + "/".join(
            urllib.parse.quote(part, safe="") for part in [mount, "data", *path.split("/")]
        )
        headers = {"X-Vault-Token": self.token, "Accept": "application/json"}
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(address + api_path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, None
            # Never include the Vault response body: it may echo sensitive input.
            raise MigrationError(f"Vault API request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MigrationError(f"Vault API request failed ({type(exc).__name__})") from None

    def read_versioned(self, mount: str, path: str) -> tuple[dict, int] | None:
        status, result = self._request("GET", mount, path)
        if status == 404:
            return None
        try:
            return result["data"]["data"], int(result["data"]["metadata"]["version"])
        except (TypeError, KeyError):
            raise MigrationError(f"Vault path {mount}/{path} is not a KV v2 data record") from None

    def read(self, mount: str, path: str) -> dict | None:
        record = self.read_versioned(mount, path)
        return record[0] if record else None

    def write(self, mount: str, path: str, values: dict, cas: int) -> None:
        self._request("POST", mount, path, {"data": values, "options": {"cas": cas}})


def parse_kv_path(value: str) -> tuple[str, str]:
    parts = value.strip("/").split("/")
    if len(parts) < 2 or any(part in {"", ".", "..", "data", "metadata", "delete", "destroy"} for part in parts):
        raise MigrationError(f"Invalid logical KV path: {value!r}")
    return parts[0], "/".join(parts[1:])


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"Cannot load migration plan ({type(exc).__name__})") from None
    if plan.get("schema_version") != 1 or not isinstance(plan.get("migrations"), list):
        raise MigrationError("Unsupported migration plan schema")
    seen: set[str] = set()
    for item in plan["migrations"]:
        source, destination = item.get("source", ""), item.get("destination", "")
        parse_kv_path(source)
        parse_kv_path(destination)
        if source == destination:
            raise MigrationError(f"Source and destination are identical: {source}")
        if source in seen:
            raise MigrationError(f"Source appears more than once: {source}")
        seen.add(source)
        if item.get("status") not in {"review_required", "approved", "keep", "retire_after_switch", "manual_recreate"}:
            raise MigrationError(f"Unsupported status for {source}")
        if not isinstance(item.get("fields", {}), dict):
            raise MigrationError(f"Field map must be an object for {source}")
        for old, new in item.get("fields", {}).items():
            if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
                raise MigrationError(f"Invalid field mapping for {source}")
    mappings = plan.get("identity_mappings", [])
    if not isinstance(mappings, list):
        raise MigrationError("identity_mappings must be an array")
    for item in mappings:
        parse_kv_path(item.get("vault_path", ""))
        for key in ("vault_field", "chart_role", "os_role", "kubernetes_subject", "kubernetes_secret", "secret_key"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise MigrationError(f"identity_mappings entry requires {key}")
    return plan


def preview(plan: dict) -> list[str]:
    lines = []
    for item in plan["migrations"]:
        fields = item.get("fields", {})
        status = item["status"]
        if status == "approved" and fields:
            action = "COPY REVIEWED FIELDS"
        elif status == "retire_after_switch":
            action = "MARK RETIRE AFTER SWITCH (NO DELETE)"
        elif status == "manual_recreate":
            action = "RECREATE CREDENTIAL AT DESTINATION"
        elif status == "keep":
            action = "KEEP IN PLACE"
        else:
            action = "PENDING FIELD/OWNER REVIEW"
        names = ", ".join(f"{old}->{new}" for old, new in fields.items()) or "(no approved field map)"
        lines.append(f"{action}: {item['source']} -> {item['destination']} [{names}]")
    for item in plan.get("retirement_candidates", []):
        lines.append(f"RETIREMENT CANDIDATE (NO DELETE): {item['path']} — gate: {item['gate']}")
    for item in plan.get("planned_destinations", []):
        lines.append(f"PLANNED DESTINATION (NO AUTOMATIC COPY): {item['path']} — {item['status']}")
    for item in plan.get("identity_mappings", []):
        lines.append(
            "IDENTITY MAP ONLY (NO CREDENTIAL INHERITANCE): "
            f"{item['vault_path']} -> chart:{item['chart_role']} "
            f"os:{item['os_role']} k8s:{item['kubernetes_subject']} "
            f"field:{item['vault_field']} secret:{item['kubernetes_secret']}#{item['secret_key']}"
        )
    for item in plan.get("never_copy_without_new_review", []):
        lines.append(f"EXCLUDED FROM AUTOMATIC COPY: {item}")
    return lines


def copy_one(source_client: VaultClient, destination_client: VaultClient, item: dict) -> str:
    src_mount, src_path = parse_kv_path(item["source"])
    dst_mount, dst_path = parse_kv_path(item["destination"])
    source = source_client.read(src_mount, src_path)
    if source is None:
        raise MigrationError(f"Source path does not exist: {item['source']}")
    mapping = item.get("fields", {})
    missing = sorted(set(mapping) - set(source))
    if missing:
        raise MigrationError(f"Source field(s) missing at {item['source']}: {', '.join(missing)}")
    selected = {target: source[origin] for origin, target in mapping.items()}
    if len(selected) != len(mapping):
        raise MigrationError(f"Multiple source fields map to one destination field at {item['destination']}")
    existing_record = destination_client.read_versioned(dst_mount, dst_path)
    if existing_record is None:
        destination_client.write(dst_mount, dst_path, selected, cas=0)
        return f"COPIED {len(selected)} reviewed field(s): {item['destination']}"
    existing, version = existing_record
    conflicts = sorted(key for key, value in selected.items() if key in existing and existing[key] != value)
    if conflicts:
        raise MigrationError(f"Destination conflict at {item['destination']}: {', '.join(conflicts)}; no write performed")
    additions = {key: value for key, value in selected.items() if key not in existing}
    if additions:
        destination_client.write(dst_mount, dst_path, {**existing, **additions}, cas=version)
        return f"ADDED {len(additions)} reviewed field(s): {item['destination']}"
    return f"NO-OP (destination fields already match): {item['destination']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="protected, owner-reviewed JSON plan; do not commit internal Vault paths")
    parser.add_argument("--apply", action="store_true", help="copy reviewed mappings; source records are never deleted")
    parser.add_argument("--check-identities", action="store_true", help="read only mapped identity records and verify field presence without printing values")
    parser.add_argument("--confirm-copy", help='required exact phrase: "COPY REVIEWED VAULT FIELDS"')
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan) if args.plan else {"schema_version": 1, "migrations": []}
        for line in preview(plan):
            print(line)
        if not args.apply:
            if args.check_identities:
                mappings = plan.get("identity_mappings", [])
                if not mappings:
                    raise MigrationError("No identity mappings are present in the plan")
                source_client = VaultClient(
                    os.environ.get("VAULT_SOURCE_ADDR", os.environ.get("VAULT_ADDR", "")),
                    os.environ.get("VAULT_SOURCE_TOKEN", os.environ.get("VAULT_TOKEN", "")),
                    os.environ.get("VAULT_SOURCE_NAMESPACE", os.environ.get("VAULT_NAMESPACE", "")),
                )
                if not source_client.address or not source_client.token:
                    raise MigrationError("Set a read-only source Vault address and token via environment variables")
                for item in mappings:
                    mount, path = parse_kv_path(item["vault_path"])
                    record = source_client.read(mount, path)
                    if record is None or item["vault_field"] not in record:
                        raise MigrationError(f"Identity field missing at {item['vault_path']}#{item['vault_field']}")
                    print(f"PASS: {item['vault_path']}#{item['vault_field']} exists; value redacted; maps to chart role {item['chart_role']}")
                print("Identity record check complete. No values were printed and no writes were made.")
                return 0
            print("Preview only. No Vault requests were made.")
            if not args.plan:
                print("Pass --plan with a protected JSON path inventory to preview specific paths.")
            return 0
        if not args.plan:
            raise MigrationError("--apply requires --plan with owner-reviewed field mappings")
        if args.check_identities:
            raise MigrationError("--check-identities cannot be combined with --apply")
        if args.confirm_copy != "COPY REVIEWED VAULT FIELDS":
            raise MigrationError('Apply requires --confirm-copy "COPY REVIEWED VAULT FIELDS"')
        approved = [item for item in plan["migrations"] if item["status"] == "approved" and item.get("fields")]
        if not approved:
            raise MigrationError("No mappings are marked approved; no Vault requests were made")
        source_client = VaultClient(
            os.environ.get("VAULT_SOURCE_ADDR", os.environ.get("VAULT_ADDR", "")),
            os.environ.get("VAULT_SOURCE_TOKEN", os.environ.get("VAULT_TOKEN", "")),
            os.environ.get("VAULT_SOURCE_NAMESPACE", os.environ.get("VAULT_NAMESPACE", "")),
        )
        destination_client = VaultClient(
            os.environ.get("VAULT_DESTINATION_ADDR", os.environ.get("VAULT_ADDR", "")),
            os.environ.get("VAULT_DESTINATION_TOKEN", os.environ.get("VAULT_TOKEN", "")),
            os.environ.get("VAULT_DESTINATION_NAMESPACE", os.environ.get("VAULT_NAMESPACE", "")),
        )
        if not source_client.address or not source_client.token or not destination_client.address or not destination_client.token:
            raise MigrationError("Set source/destination Vault address and token via environment variables")
        for item in approved:
            print(copy_one(source_client, destination_client, item))
        print("Copy complete. Source records retained; retirement requires separate consumer and rollback review.")
        return 0
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
