#!/usr/bin/env python3
"""Require the checked-out owner to match the caller's immutable pin."""

import os
import re
import subprocess
import sys


def main():
    expected = os.environ.get("EXPECTED_OWNER_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("owner_sha must be a full lowercase commit SHA")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if actual != expected:
        raise ValueError("Playbooks owner checkout does not match owner_sha")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(2)
