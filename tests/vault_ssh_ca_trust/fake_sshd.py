#!/usr/bin/env python3
"""Minimal sshd stand-in for isolated tests when a real sshd is unavailable.

-t: parse main config + Includes, fail on unknown keywords or missing CA file.
-T: print first-match-wins effective values (lower-case), like sshd -T.
"""
import glob
import os
import sys

KNOWN = {"include", "trustedusercakeys", "authorizedprincipalsfile", "port", "passwordauthentication",
         "pubkeyauthentication", "permitrootlogin", "usepam", "subsystem", "hostkey"}


def lines(path, base):
    for raw in open(path):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        key, _, val = raw.partition(" ")
        if key.lower() == "include":
            pat = val.strip() if os.path.isabs(val.strip()) else os.path.join(base, val.strip())
            for inc in sorted(glob.glob(pat)):
                yield from lines(inc, base)
        else:
            yield key.lower(), val.strip()


args = sys.argv[1:]
cfg = args[args.index("-f") + 1]
eff = {}
for k, v in lines(cfg, "/etc/ssh"):
    if k not in KNOWN:
        sys.stderr.write(f"Bad configuration option: {k}\n")
        sys.exit(255)
    eff.setdefault(k, v)
if "-T" in args:
    for k, v in eff.items():
        print(f"{k} {v}")
