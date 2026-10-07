#!/usr/bin/env python3
"""Translate an authorized Vault response into private Role variables."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path


FIELDS = {
    "fullchain": "tls_fullchain_pem_b64",
    "cert": "tls_cert_pem_b64",
    "key": "tls_key_pem_b64",
    "ca": "tls_ca_pem_b64",
    "trust_bundle": "tls_trust_bundle_pem_b64",
}


def emit(output_file: Path, values: dict[str, str]) -> None:
    with output_file.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def material(record: dict) -> dict[str, str]:
    decoded = {}
    for key, field in FIELDS.items():
        decoded[key] = base64.b64decode(
            "".join(str(record[field]).split()), validate=True
        ).decode("utf-8")
    return decoded


def plan(args: argparse.Namespace) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.target):
        raise ValueError("exact restore target required")
    if not re.fullmatch(r"/etc/xcontrol/tls/[A-Za-z0-9][A-Za-z0-9_.-]*", args.directory):
        raise ValueError("approved single-domain restore directory required")
    if args.margin_days < 0:
        raise ValueError("renewal margin cannot be negative")
    if args.http_status == 404:
        return {"restore_required": "false", "reason": "no-backup", "vars_file": ""}
    if args.http_status != 200:
        raise ValueError(f"Vault read returned HTTP {args.http_status}")

    response = json.loads(args.vault_response.read_text(encoding="utf-8"))
    record = ((response.get("data") or {}).get("data") or {})
    if not all(record.get(field) for field in FIELDS.values()):
        return {"restore_required": "false", "reason": "incomplete-backup", "vars_file": ""}
    expiry = record.get("not_after_epoch")
    margin_seconds = args.margin_days * 86400
    if expiry not in (None, "") and int(expiry) - time.time() < margin_seconds:
        return {"restore_required": "false", "reason": "renewal-margin", "vars_file": ""}

    variables = {
        "caddy_certificate_restore_target": args.target,
        "caddy_certificate_restore_directory": args.directory,
        "caddy_certificate_restore_min_validity_seconds": margin_seconds,
        "caddy_certificate_restore_material": material(record),
    }
    args.vars_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.vars_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(variables, stream)
    except Exception:
        args.vars_file.unlink(missing_ok=True)
        raise
    return {"restore_required": "true", "reason": "ready", "vars_file": str(args.vars_file)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-response", type=Path, required=True)
    parser.add_argument("--http-status", type=int, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--margin-days", type=int, default=14)
    parser.add_argument("--vars-file", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        outputs = plan(args)
        emit(args.github_output, outputs)
        print(f"Caddy certificate restore plan: {outputs['reason']}")
        return 0
    except Exception as error:
        # Values and decoder errors can contain secret material. Keep the
        # diagnostic at the contract boundary without echoing exception data.
        print(f"Caddy restore plan refused the authorized response ({type(error).__name__}).", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
