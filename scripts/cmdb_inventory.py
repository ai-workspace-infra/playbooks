#!/usr/bin/env python3
"""Ansible dynamic inventory script for CMDB service.

Fetches inventory from https://$CMDB_DOMAIN/api/v1/inventory using $CMDB_API_TOKEN.
Credentials can be provided via environment variables or loaded from /opt/ansible-cmdb/cmdb.env.

Usage:
  cmdb_inventory.py --list
  cmdb_inventory.py --host <hostname>
  ansible-inventory -i /opt/ansible-cmdb/cmdb_inventory.py --list
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_env_file(filepath):
    """Parse simple KEY=VALUE configuration file."""
    env = {}
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip("'\"")
        except OSError:
            pass
    return env


def get_config():
    """Retrieve CMDB connection parameters."""
    env_paths = [
        os.environ.get("CMDB_ENV_FILE", ""),
        "/opt/ansible-cmdb/cmdb.env",
        "/etc/ansible-cmdb/cmdb.env",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmdb.env"),
    ]

    file_env = {}
    for p in env_paths:
        if p and os.path.isfile(p):
            file_env = load_env_file(p)
            break

    domain = (
        os.environ.get("CMDB_DOMAIN")
        or file_env.get("CMDB_DOMAIN")
        or ""
    ).strip()

    token = (
        os.environ.get("CMDB_API_TOKEN")
        or file_env.get("CMDB_API_TOKEN")
        or ""
    ).strip()

    url = (
        os.environ.get("CMDB_URL")
        or file_env.get("CMDB_URL")
        or ""
    ).strip()

    insecure = (
        os.environ.get("CMDB_INSECURE")
        or file_env.get("CMDB_INSECURE")
        or ""
    ).strip().lower() in ("1", "true", "yes")

    if not url and domain:
        if domain.startswith("http://") or domain.startswith("https://"):
            url = f"{domain.rstrip('/')}/api/v1/inventory"
        else:
            url = f"https://{domain}/api/v1/inventory"

    return {
        "domain": domain,
        "token": token,
        "url": url,
        "insecure": insecure,
    }


def fetch_inventory(config):
    """Fetch inventory JSON from CMDB API endpoint."""
    url = config["url"]
    token = config["token"]

    if not url:
        sys.stderr.write(
            "Error: CMDB domain/URL not configured. "
            "Set CMDB_DOMAIN or CMDB_URL environment variable or configure /opt/ansible-cmdb/cmdb.env\n"
        )
        sys.exit(1)

    if not token:
        sys.stderr.write(
            "Error: CMDB_API_TOKEN not configured. "
            "Set CMDB_API_TOKEN environment variable or configure /opt/ansible-cmdb/cmdb.env\n"
        )
        sys.exit(1)

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ansible-cmdb-inventory/1.0",
        },
        method="GET",
    )

    ctx = ssl.create_default_context()
    if config["insecure"]:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP Error {e.code} while fetching inventory from {url}: {e.reason}\n")
        sys.exit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(f"Connection Error while connecting to {url}: {e.reason}\n")
        sys.exit(1)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Error parsing CMDB response JSON from {url}: {e}\n")
        sys.exit(1)


def main():
    args = sys.argv[1:]
    config = get_config()

    if "--help" in args or "-h" in args:
        print(__doc__.strip())
        sys.exit(0)

    if "--host" in args:
        try:
            host_idx = args.index("--host") + 1
            host_name = args[host_idx]
        except IndexError:
            sys.stderr.write("Error: --host requires a hostname argument\n")
            sys.exit(1)
        data = fetch_inventory(config)
        hostvars = data.get("_meta", {}).get("hostvars", {}).get(host_name, {})
        print(json.dumps(hostvars, indent=2))
        return

    # Default action or --list
    data = fetch_inventory(config)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
