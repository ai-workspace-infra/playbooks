#!/usr/bin/env python3
"""Execute the direct import owner without exposing captured runtime output."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess

def summarize(stdout, stderr, returncode, script, dry_run, timed_out=False):
    combined = stdout + stderr
    phase = "target_tunnel" if "tunnel" in script else "credentials"
    if "[STEP 1/4]" in stdout:
        phase = "source_export"
    if "[STEP 2/4]" in stdout:
        phase = "target_preview"
    if "[STEP 3/4]" in stdout and not dry_run:
        phase = "target_apply"
    if "[STEP 4/4] Verifying convergence" in stdout and not dry_run:
        phase = "target_verify"
    verified = (returncode == 0 and not timed_out and not dry_run
                and "[STEP 4/4] Verification passed:" in stdout)
    write_state = ("verified" if verified else "unverified"
                   if phase in {"target_apply", "target_verify"} else "not_attempted")
    category = "success" if returncode == 0 else "execution_failed"
    for pattern, label in [("Permission denied", "ssh_authentication"),
                       ("Connection timed out", "ssh_timeout"),
                       ("password authentication failed", "database_authentication"),
                       ("permission denied for", "database_permission"),
                       ("does not exist", "database_schema"),
                       ("unexpected EOF", "database_connection"),
                       ("unexpected eof", "database_connection"),
                       ("failed to connect", "database_connection")]:
        if returncode != 0 and pattern in combined:
            category = label
            break
    if timed_out:
        category = "execution_timeout"
    states = re.findall(r"SQLSTATE\s+([A-Z0-9]{5})", combined)
    return {"phase": phase, "category": category, "sqlstate": states[-1] if states else "",
            "write_state": write_state, "convergence_verified": verified}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script", choices=["accounts_data_migration.sh", "accounts_data_migration_target_tunnel.sh"])
    args = parser.parse_args()
    script = Path(__file__).resolve().parent / args.script
    process = subprocess.Popen(["bash", str(script)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    receipt = summarize(stdout, stderr, process.returncode, args.script,
                        os.environ.get("DRY_RUN") == "true", timed_out)
    path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "uat-import-runtime.json"
    path.write_text(json.dumps(receipt) + "\n")
    return 0 if process.returncode == 0 and not timed_out else 1


if __name__ == "__main__":
    raise SystemExit(main())
