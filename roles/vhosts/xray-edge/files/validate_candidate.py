#!/usr/bin/env python3
"""Validate a candidate Caddy site against all currently imported fragments."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


IMPORT_RE = re.compile(
    r"^(?P<indent>\s*)import\s+(?P<path>(?:\S*/)?conf\.d/\*\.caddy)\s*$"
)


def shadow_config(main: Path, conf_dir: Path, managed_name: str, candidate: Path) -> str:
    content = main.read_text(encoding="utf-8")
    imported_lines = []
    for line in content.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        match = IMPORT_RE.match(bare)
        if not match:
            imported_lines.append(line)
            continue

        import_path = Path(match.group("path"))
        if not import_path.is_absolute():
            import_path = (main.parent / import_path).resolve()
        expected_pattern = (conf_dir / "*.caddy").resolve()
        if import_path.resolve() != expected_pattern:
            imported_lines.append(line)
            continue

        fragments = {
            item.name: item.resolve()
            for item in conf_dir.glob("*.caddy")
            if item.name != managed_name
        }
        fragments[managed_name] = candidate.resolve()
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        prefix = match.group("indent")
        imported_lines.extend(
            f'{prefix}import "{fragments[name]}"{newline}'
            for name in sorted(fragments)
        )

    shadow = "".join(imported_lines)
    if shadow == content:
        raise ValueError("main Caddyfile does not import the expected conf.d/*.caddy glob")
    return shadow


def validate(main: Path, conf_dir: Path, managed_name: str, candidate: Path, caddy: str) -> int:
    if Path(managed_name).name != managed_name or not managed_name.endswith(".caddy"):
        raise ValueError("managed site name must be a plain .caddy filename")
    if not candidate.is_file():
        raise ValueError("candidate Caddy site is missing")
    shadow = shadow_config(main, conf_dir, managed_name, candidate)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=main.parent, prefix=".xhttp-edge-validate-", suffix=".Caddyfile", delete=False
        ) as temporary:
            temporary.write(shadow)
            temporary_path = Path(temporary.name)
        result = subprocess.run(
            [caddy, "validate", "--config", str(temporary_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            print("Combined Caddy validation failed; configuration output is suppressed.", file=sys.stderr)
            return result.returncode
        print("Combined Caddy configuration is valid.")
        return 0
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--conf-dir", required=True, type=Path)
    parser.add_argument("--managed-name", required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--caddy", required=True)
    args = parser.parse_args()
    try:
        return validate(args.main, args.conf_dir, args.managed_name, args.candidate, args.caddy)
    except (OSError, ValueError) as error:
        print(f"Candidate validation precondition failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
