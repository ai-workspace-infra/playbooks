#!/bin/bash
set -eo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../lib/require-env.sh"

require_env MIGRATION_SOURCE_DSN MIGRATION_TARGET_DSN

# Parse credentials without printing them. Only fixed diagnostic messages may
# reach public workflow logs; do not echo exception text or connection URLs.
python3 - <<'VALIDATE'
import os
import sys
from urllib.parse import urlsplit, parse_qs

errors = []
def endpoint(name):
    try:
        value = urlsplit(os.environ[name])
        if value.scheme not in {"postgres", "postgresql"} or not value.hostname:
            raise ValueError
        value.port
        if set(parse_qs(value.query)) - {"sslmode", "connect_timeout"}:
            raise ValueError
        return value
    except ValueError:
        errors.append(f"{name}: invalid PostgreSQL URL")
        return None

source = endpoint("MIGRATION_SOURCE_DSN")
target = endpoint("MIGRATION_TARGET_DSN")
if source:
    project = os.environ.get("MIGRATION_SOURCE_PROJECT_REF", "")
    supabase = bool(project) and source.hostname.endswith((".supabase.co", ".supabase.com")) and source.username in {"readonly", "readonly." + project} and (source.hostname == "db." + project + ".supabase.co" or source.username == "readonly." + project)
    if not source.hostname.endswith(".svc.plus") and not supabase:
        errors.append("MIGRATION_SOURCE_DSN: direct legacy import requires a verified PROD host under svc.plus; Supabase requires a reviewed source identity contract")
    if source.username != "readonly" and not (supabase and source.username == "readonly." + project):
        errors.append("MIGRATION_SOURCE_DSN: source must use the readonly role; administrator credentials are prohibited")
if target:
    if not target.hostname.endswith(".onwalk.net"):
        errors.append("MIGRATION_TARGET_DSN: direct legacy import requires a UAT host under onwalk.net; private addresses require a reviewed transport contract")
if source and target and (source.hostname, source.port, source.path) == (target.hostname, target.port, target.path):
    errors.append("Migration source and target identify the same database")
if errors:
    for message in errors:
        print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)
print("Migration credentials present: source=PROD(readonly), target=UAT.")
VALIDATE
