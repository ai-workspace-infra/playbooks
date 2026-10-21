#!/usr/bin/env python3
"""Render isolated acceptance hostnames; creates no DNS records."""
import argparse
import json
import os
import re


def domains(environment, deployment_id, base):
    if environment not in ("sit", "uat"):
        raise ValueError("Acceptance ENV must be sit or uat")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", deployment_id):
        raise ValueError("DEPLOYMENT_ID must be a lowercase DNS label")
    if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", base):
        raise ValueError("DOMAIN_BASE must be an explicit DNS suffix")
    result = {}
    for service in ("console", "accounts", "billing", "postgresql"):
        label = f"{service}-{environment}-{deployment_id}"
        if len(label) > 63 or len(label + '.' + base) > 253:
            raise ValueError("Generated hostname exceeds DNS limits")
        result[service] = label + '.' + base
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=os.environ.get("ENV", "sit"))
    parser.add_argument("--deployment-id", default=os.environ.get("DEPLOYMENT_ID"))
    parser.add_argument("--domain-base", default=os.environ.get("DOMAIN_BASE"))
    args = parser.parse_args()
    if not args.deployment_id or not args.domain_base:
        parser.error("DEPLOYMENT_ID and DOMAIN_BASE are required")
    try:
        print(json.dumps(domains(args.env, args.deployment_id, args.domain_base), indent=2))
    except ValueError as exc:
        parser.error(str(exc))
