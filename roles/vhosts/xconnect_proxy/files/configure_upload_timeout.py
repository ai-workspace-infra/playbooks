#!/usr/bin/env python3
"""Preserve node configuration; change only level-zero idle timeout."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile


def apply(root, seconds):
    base = Path(root)
    templates = [base / "templates/xray.xhttp.template.json", base / "templates/xray.tcp.template.json"]
    runtime = [base / "config.json", base / "tcp-config.json"]
    # Read everything before any mutation; missing or invalid files fail closed.
    documents = {p: json.loads(p.read_text()) for p in templates + runtime}
    changed = []
    backup = None
    for path in templates:
        data = documents[path]
        level = data.setdefault("policy", {}).setdefault("levels", {}).setdefault("0", {})
        if level.get("connIdle") == seconds:
            continue
        if backup is None:
            backup = base / ("upload-timeout-backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            backup.mkdir(mode=0o700)
        shutil.copy2(path, backup / path.name)
        level["connIdle"] = seconds
        stat = path.stat()
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-timeout-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.st_mode & 0o777)
            os.chown(temporary, stat.st_uid, stat.st_gid)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        changed.append(path.name)
    drift = any(documents[p].get("policy", {}).get("levels", {}).get("0", {}).get("connIdle") != seconds for p in runtime)
    return {"changed_templates": changed, "reload_required": bool(changed) or drift, "desired_connIdle": seconds}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/usr/local/etc/xray")
    parser.add_argument("--seconds", type=int, required=True)
    args = parser.parse_args()
    if not 300 <= args.seconds <= 86400:
        parser.error("seconds must be between 300 and 86400")
    print(json.dumps(apply(args.root, args.seconds)))
