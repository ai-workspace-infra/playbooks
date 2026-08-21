#!/usr/bin/env python3
"""Patch Xray metrics API endpoints without touching proxy inbounds."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def patch_config(
    path: Path,
    api_port: int,
    certificate_file: str | None = None,
    key_file: str | None = None,
) -> tuple[dict, bool]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    api_inbounds = [item for item in data.get("inbounds", []) if item.get("tag") == "api"]
    if len(api_inbounds) != 1:
        raise RuntimeError(f"{path}: expected exactly one api inbound, found {len(api_inbounds)}")

    api_inbound = api_inbounds[0]
    if api_inbound.get("listen") != "127.0.0.1":
        raise RuntimeError(f"{path}: refusing to patch non-loopback api inbound")

    changed = api_inbound.get("port") != api_port
    api_inbound["port"] = api_port

    if certificate_file is not None or key_file is not None:
        certificates = []
        for inbound in data.get("inbounds", []):
            certificates.extend(
                inbound.get("streamSettings", {}).get("tlsSettings", {}).get("certificates", [])
            )
        if len(certificates) != 1:
            raise RuntimeError(f"{path}: expected exactly one TLS certificate, found {len(certificates)}")
        certificate = certificates[0]
        if certificate.get("certificateFile") != certificate_file:
            changed = True
            certificate["certificateFile"] = certificate_file
        if certificate.get("keyFile") != key_file:
            changed = True
            certificate["keyFile"] = key_file

    return data, changed


def write_candidate(path: Path, data: dict) -> Path:
    mode = path.stat().st_mode & 0o777
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent)
    candidate = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(candidate, mode)
        return candidate
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def validate(binary: str, candidate: Path) -> None:
    result = subprocess.run(
        [binary, "run", "-test", "-config", str(candidate)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"Xray rejected {candidate}:\n{output}")


def is_immutable(path: Path) -> bool:
    """Return whether Linux marks a config file immutable."""
    result = subprocess.run(
        ["lsattr", "-d", str(path)],
        text=True,
        capture_output=True,
        check=True,
    )
    attributes = result.stdout.split(maxsplit=1)[0]
    return len(attributes) > 4 and attributes[4] == "i"


def set_immutable(path: Path, enabled: bool) -> None:
    """Temporarily unlock a protected config, then restore its prior state."""
    subprocess.run(
        ["chattr", "+i" if enabled else "-i", str(path)],
        check=True,
        text=True,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xhttp-config", required=True, type=Path)
    parser.add_argument("--tcp-config", required=True, type=Path)
    parser.add_argument("--xray-binary", required=True)
    parser.add_argument("--xhttp-api-port", required=True, type=int)
    parser.add_argument("--tcp-api-port", required=True, type=int)
    parser.add_argument("--tcp-certificate-file", required=True)
    parser.add_argument("--tcp-key-file", required=True)
    args = parser.parse_args()

    for required in (
        args.xhttp_config,
        args.tcp_config,
        Path(args.xray_binary),
        Path(args.tcp_certificate_file),
        Path(args.tcp_key_file),
    ):
        if not required.exists():
            raise RuntimeError(f"required path does not exist: {required}")

    xhttp_data, xhttp_changed = patch_config(args.xhttp_config, args.xhttp_api_port)
    tcp_data, tcp_changed = patch_config(
        args.tcp_config,
        args.tcp_api_port,
        args.tcp_certificate_file,
        args.tcp_key_file,
    )

    if not xhttp_changed and not tcp_changed:
        print("No changes required")
        return 0

    candidates = [
        (args.xhttp_config, write_candidate(args.xhttp_config, xhttp_data)),
        (args.tcp_config, write_candidate(args.tcp_config, tcp_data)),
    ]
    immutable = {original: is_immutable(original) for original, _ in candidates}
    try:
        for _, candidate in candidates:
            validate(args.xray_binary, candidate)

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        for original, protected in immutable.items():
            if protected:
                set_immutable(original, False)
        for original, candidate in candidates:
            backup = original.with_name(f"{original.name}.bak.xray-exporter.{stamp}")
            shutil.copy2(original, backup)
            os.replace(candidate, original)
            print(f"Patched {original}; backup={backup}")
    finally:
        for _, candidate in candidates:
            candidate.unlink(missing_ok=True)
        for original, protected in immutable.items():
            if protected:
                set_immutable(original, True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
