#!/usr/bin/env python3
"""Validate a Supabase session-pooler target and require TLS without printing secrets."""

import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit


try:
    raw = os.environ["TARGET_DSN"]
    project_ref = os.environ["PROJECT_REF"]
    environment = os.environ["VAULT_ENV_PATH"]
    url = urlsplit(raw)
    query = parse_qs(url.query, keep_blank_values=True)
    modes = query.get("sslmode", [])
    checks = {
        "scheme": url.scheme in {"postgres", "postgresql"},
        "environment": environment in {"uat", "prod"},
        "project_user": unquote(url.username or "") == "postgres." + project_ref,
        "session_pooler_host": (url.hostname or "").endswith(".pooler.supabase.com"),
        "port_5432": url.port == 5432,
        "database_postgres": url.path == "/postgres",
        "tls_mode": not modes or (len(modes) == 1 and modes[0] in {"require", "verify-ca", "verify-full"}),
        "no_fragment": not url.fragment,
    }
except (KeyError, ValueError):
    checks = {"url_parse": False}

if not all(checks.values()):
    print("Supabase target format checks: " + ", ".join(f"{key}={value}" for key, value in checks.items()), file=sys.stderr)
    raise SystemExit(1)

# A session-pooler URL may omit sslmode. Append it only after all target
# checks pass; an explicit insecure/unknown mode is always rejected.
if not modes:
    url = url._replace(query=url.query + ("&" if url.query else "") + "sslmode=require")
sys.stdout.write(urlunsplit(url))
