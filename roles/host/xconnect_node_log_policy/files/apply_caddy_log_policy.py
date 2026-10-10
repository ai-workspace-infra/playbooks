#!/usr/bin/env python3
"""Add the XConnect reverse_proxy ERROR-only Caddy log policy safely."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BEGIN = "# BEGIN XConnect reverse_proxy log policy"
END = "# END XConnect reverse_proxy log policy"
BLOCK = f"""{BEGIN}
\tlog default {{
\t\texclude http.handlers.reverse_proxy
\t}}
\tlog rp {{
\t\tinclude http.handlers.reverse_proxy
\t\tlevel ERROR
\t}}
{END}"""


def render(text: str) -> tuple[str, bool]:
    if BEGIN in text or "http.handlers.reverse_proxy" in text:
        return text, False

    lines = text.splitlines(keepends=True)
    first_code = next(
        (index for index, line in enumerate(lines)
         if line.strip() and not line.lstrip().startswith("#")),
        None,
    )
    if first_code is not None and lines[first_code].strip() == "{":
        # Caddy's global options block must not receive a second default logger.
        block_end = next(
            (index for index in range(first_code + 1, len(lines))
             if lines[index].strip() == "}"),
            None,
        )
        if block_end is None:
            raise ValueError("Caddy global options block is not closed")
        if any(re.match(r"^\s*log(?:\s|\{)", line)
               for line in lines[first_code + 1:block_end]):
            raise ValueError("Caddy global block already defines log directives")
        newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        block = BLOCK.replace("\n", newline) + newline
        lines.insert(first_code + 1, block)
        return "".join(lines), True

    prefix = "{\n" + BLOCK + "\n}\n\n"
    return prefix + text, True


def apply(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    candidate, changed = render(original)
    if not changed:
        print("unchanged: reverse_proxy logging policy already exists")
        return False

    caddy = shutil.which("caddy")
    if not caddy:
        raise RuntimeError("caddy executable is unavailable")

    fd, temp_name = tempfile.mkstemp(prefix=".Caddyfile.xconnect-", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    backup = path.with_name(
        f"{path.name}.xconnect-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.bak"
    )
    try:
        temp_path.write_text(candidate, encoding="utf-8")
        temp_path.chmod(stat.S_IMODE(path.stat().st_mode))
        validation = subprocess.run(
            [caddy, "validate", "--config", str(temp_path), "--adapter", "caddyfile"],
            text=True,
            capture_output=True,
            check=False,
        )
        if validation.returncode:
            diagnostic = (validation.stderr or validation.stdout).strip()
            raise RuntimeError(f"Caddy validation failed: {diagnostic[-1200:]}")

        shutil.copy2(path, backup)
        os.replace(temp_path, path)
        reload_result = subprocess.run(
            ["systemctl", "reload", "caddy"], text=True, capture_output=True, check=False
        )
        if reload_result.returncode:
            shutil.copy2(backup, path)
            subprocess.run(["systemctl", "reload", "caddy"], capture_output=True, check=False)
            raise RuntimeError("Caddy reload failed; the original Caddyfile was restored")
    finally:
        temp_path.unlink(missing_ok=True)

    print(f"changed: Caddy reloaded; backup={backup}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("caddyfile", type=Path)
    args = parser.parse_args()
    try:
        changed = apply(args.caddyfile)
    except Exception as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
