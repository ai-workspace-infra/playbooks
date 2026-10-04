#!/usr/bin/env python3
"""Read-only UAT readiness diagnostics; never a passing business acceptance proof."""
import argparse
import json
import os
import re
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit

TABLES = ("users", "identities", "subscriptions", "schema_migrations")


class Blocked(Exception):
    pass


def connection_env(environment):
    if environment.get("VAULT_ENV_PATH") != "uat":
        raise Blocked("probe_requires_uat")
    project = environment.get("PROJECT_REF", "")
    try:
        url = urlsplit(environment.get("TARGET_DSN", ""))
        modes = parse_qs(url.query, keep_blank_values=True).get("sslmode", [])
        valid = (re.fullmatch(r"[a-z0-9]{20}", project)
                 and url.scheme in {"postgres", "postgresql"}
                 and (url.hostname or "").endswith(".pooler.supabase.com")
                 and url.port == 5432 and url.path == "/postgres" and not url.fragment
                 and unquote(url.username or "") == "postgres." + project
                 and bool(url.password)
                 and (not modes or (len(modes) == 1 and modes[0] in {"require", "verify-ca", "verify-full"})))
    except ValueError:
        valid = False
    if not valid:
        raise Blocked("uat_connection_identity_or_tls_invalid")
    return {**environment, "PGHOST": url.hostname, "PGPORT": "5432",
            "PGUSER": unquote(url.username), "PGPASSWORD": unquote(url.password),
            "PGDATABASE": "postgres", "PGSSLMODE": modes[0] if modes else "require",
            "PGCONNECT_TIMEOUT": "10", "PGOPTIONS": ""}


def read_query(sql, environment):
    # Separate protocol commands on one connection. Startup PGOPTIONS alone
    # does not reliably survive Session Pooler configuration resets.
    command = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-A", "-t",
               "-c", "BEGIN READ ONLY",
               "-c", "SET LOCAL statement_timeout='10s'; SET LOCAL lock_timeout='2s'",
               "-c", sql, "-c", "COMMIT"]
    try:
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, timeout=25, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        # Never relay errors containing usernames, connection identities or DSNs.
        raise Blocked("database_connection_or_query_failed") from None


def collect(query):
    metadata = json.loads(query("SELECT json_build_object('transaction_read_only', current_setting('transaction_read_only'), 'tables', (SELECT coalesce(json_agg(table_name),'[]') FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','identities','subscriptions','schema_migrations')))::text"))
    if metadata.get("transaction_read_only") != "on":
        raise Blocked("read_only_transaction_not_enforced")
    available = metadata.get("tables")
    if not isinstance(available, list) or any(table not in TABLES for table in available):
        raise Blocked("invalid_table_metadata")
    report = {"transaction_read_only": True, "counts": {}, "migration": None}
    for table in TABLES:
        if table not in available:
            report["counts"][table] = None
            continue
        raw = query(f"SELECT count(*) FROM public.{table}")
        if not re.fullmatch(r"[0-9]+", raw):
            raise Blocked("invalid_count_result")
        report["counts"][table] = int(raw)
    if report["counts"].get("schema_migrations") == 1:
        record = json.loads(query("SELECT json_build_object('version',version::text,'dirty',dirty)::text FROM public.schema_migrations"))
        if (not re.fullmatch(r"[1-9][0-9]*", str(record.get("version", "")))
                or type(record.get("dirty")) is not bool):
            raise Blocked("invalid_migration_result")
        report["migration"] = {"version": int(record["version"]), "dirty": record["dirty"]}
    return report


def summarize(observation, expected):
    blockers = []
    counts = observation["counts"]
    for table in ("users", "subscriptions"):
        if not isinstance(counts.get(table), int) or counts[table] <= 0:
            blockers.append(f"nonempty_{table}_sample_required")
    migration = observation.get("migration")
    if counts.get("schema_migrations") != 1 or not migration:
        blockers.append("recognized_migration_tracking_required")
    elif migration["dirty"]:
        blockers.append("migration_dirty")
    elif migration["version"] != expected:
        blockers.append("artifact_migration_target_not_reached")
    blockers += ["old_immutable_baseline_not_verified", "original_user_login_not_executed",
                 "effective_permissions_not_verified", "subscription_api_quota_charge_not_verified",
                 "serving_digest_and_migration_idempotence_not_verified"]
    return {"schema": 1, "environment": "uat", "stage": "readiness_diagnostic",
            "expected_schema_version": expected, "observation": observation,
            "gates": {name: "blocked" for name in
                      ("smooth_upgrade", "original_user_login", "subscriptions_preserved")},
            "blockers": blockers, "eligible_for_prod": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True, type=int)
    args = parser.parse_args()
    if args.expected_version <= 0:
        parser.error("expected-version must be positive")
    try:
        environment = connection_env(os.environ)
        report = summarize(collect(lambda sql: read_query(sql, environment)), args.expected_version)
    except (Blocked, json.JSONDecodeError, TypeError, KeyError) as error:
        # No credential strings, query payloads or arbitrary backend messages.
        reason = str(error) if isinstance(error, Blocked) else "invalid_probe_result"
        print(json.dumps({"environment": "uat", "stage": "readiness_diagnostic",
                          "eligible_for_prod": False, "error": reason}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 1  # Even converged structural checks are not business acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
