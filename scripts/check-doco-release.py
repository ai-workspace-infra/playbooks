#!/usr/bin/env python3
"""Require a completed Doco-CD job for one immutable GitOps commit."""
import argparse
import json
import subprocess
import sys


def completed_release(lines, revision, target):
    jobs = set()
    completed = set()
    for line in lines:
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        job = item.get("job_id")
        if not job:
            continue
        if item.get("msg") == "source prepared" and item.get("revision") == revision and item.get("target") == target:
            jobs.add(job)
        if item.get("msg") == "job completed successfully" and item.get("target") == target:
            completed.add(job)
    return bool(jobs & completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--target", default="prod-v2")
    args = parser.parse_args()
    try:
        logs = subprocess.check_output(
            ["docker", "logs", "--since", "30m", "doco-cd"],
            stderr=subprocess.STDOUT, timeout=20, text=True)
        ok = completed_release(logs.splitlines(), args.revision, args.target)
    except Exception:
        ok = False
    print(json.dumps({"ok": ok, "stage": "doco_exact_revision_completed"}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
