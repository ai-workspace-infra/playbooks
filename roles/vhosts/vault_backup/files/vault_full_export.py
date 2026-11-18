#!/usr/bin/env python3
"""Full read-only export of Vault KV v2 + policies + auth roles.

Prints ONLY structural progress (paths, counts) to stderr -- never a secret value.
The dump goes straight to the output file, which is created 0600.
"""
import json
import os
import subprocess
import sys

ADDR = os.environ.get("VAULT_ADDR", "https://vault.svc.plus")
OUT = sys.argv[1]


def vault(*args):
    r = subprocess.run(
        ["vault", *args],
        capture_output=True, text=True,
        env={**os.environ, "VAULT_ADDR": ADDR},
    )
    if r.returncode != 0:
        return None
    return r.stdout


def vjson(*args):
    # Vault CLI rejects flags placed after positional args, so -format=json goes
    # immediately after the subcommand.
    sub = [a for a in args if not a.startswith("-")]
    cmd = sub[:-1] + ["-format=json", sub[-1]]
    out = vault(*cmd)
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def walk(prefix=""):
    """Recursively collect every KV v2 secret path under kv/."""
    listing = vjson("kv", "list", f"kv/{prefix}")
    paths = []
    if not listing:
        return paths
    for entry in listing:
        if entry.endswith("/"):
            paths.extend(walk(prefix + entry))
        else:
            paths.append(prefix + entry)
    return paths


def main():
    dump = {"vault_addr": ADDR, "kv": {}, "policies": {}, "jwt_roles": {}, "meta": {}}

    print("walking kv/ ...", file=sys.stderr)
    paths = walk()
    print(f"  found {len(paths)} secret paths", file=sys.stderr)

    ok = fail = 0
    for p in paths:
        data = vjson("kv", "get", f"kv/{p}")
        if data and "data" in data:
            # keep both the values and the version metadata
            dump["kv"][p] = {
                "data": data["data"].get("data"),
                "metadata": data["data"].get("metadata"),
            }
            ok += 1
        else:
            dump["kv"][p] = {"error": "read failed"}
            fail += 1
    print(f"  exported {ok} ok, {fail} failed", file=sys.stderr)

    # policies (not secret, but required for disaster recovery)
    pol = vjson("policy", "list") or []
    for name in pol:
        body = vault("policy", "read", name)
        if body is not None:
            dump["policies"][name] = body
    print(f"  policies: {len(dump['policies'])}", file=sys.stderr)

    # jwt auth roles
    roles = vjson("list", "auth/jwt/role") or []
    for name in roles:
        r = vjson("read", f"auth/jwt/role/{name}")
        if r:
            dump["jwt_roles"][name] = r.get("data")
    print(f"  jwt roles: {len(dump['jwt_roles'])}", file=sys.stderr)

    dump["meta"] = {
        "secret_count": ok,
        "failed_count": fail,
        "policy_count": len(dump["policies"]),
        "jwt_role_count": len(dump["jwt_roles"]),
    }

    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(dump, f, indent=2, sort_keys=True, ensure_ascii=False)
    print(f"written: {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
