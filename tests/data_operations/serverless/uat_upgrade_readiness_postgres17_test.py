#!/usr/bin/env python3
"""Disposable localhost fixture only; never run against a UAT connection."""
import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

path = Path(__file__).resolve().parents[3] / "scripts/data_operations/serverless/probe_uat_upgrade_readiness.py"
spec = importlib.util.spec_from_file_location("readiness", path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
env = {**os.environ, "PGSSLMODE": "disable", "PGDATABASE": "postgres", "PGCONNECT_TIMEOUT": "5"}
if env.get("PGHOST") not in {"127.0.0.1", "localhost"} or env.get("PGUSER") != "postgres":
    raise SystemExit("Integration test requires the disposable localhost postgres service.")


def write(sql, connection):
    return subprocess.check_output(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
                                   env=connection, stderr=subprocess.DEVNULL, text=True).strip()


if write("SELECT current_setting('server_version_num')::int / 10000", env) != "17":
    raise SystemExit("Integration test requires PostgreSQL 17.")
database = "uat_readiness_fixture_" + uuid.uuid4().hex
write(f"CREATE DATABASE {database}", env)
fixture = {**env, "PGDATABASE": database}
checks = 0
try:
    write("CREATE TABLE users(password text); CREATE TABLE identities(id int); CREATE TABLE subscriptions(id int); CREATE TABLE schema_migrations(version bigint, dirty boolean); INSERT INTO users VALUES ('fixture-only'); INSERT INTO identities VALUES (1); INSERT INTO schema_migrations VALUES (2026092703,false)", fixture)
    observe = lambda: probe.collect(lambda sql: probe.read_query(sql, fixture))
    before = observe()
    assert before["transaction_read_only"] is True
    checks += 1
    report = probe.summarize(before, 2026092801)
    assert "nonempty_subscriptions_sample_required" in report["blockers"]
    checks += 1
    assert "artifact_migration_target_not_reached" in report["blockers"]
    checks += 1
    try:
        probe.read_query("UPDATE users SET password='must-not-write'", fixture)
    except probe.Blocked:
        pass
    else:
        raise AssertionError("Explicit read-only transaction allowed a write")
    assert write("SELECT password='fixture-only' FROM users", fixture) == "t"
    checks += 1
    write("INSERT INTO subscriptions VALUES (1); UPDATE schema_migrations SET version=2026092801", fixture)
    report = probe.summarize(observe(), 2026092801)
    assert report["eligible_for_prod"] is False and set(report["gates"].values()) == {"blocked"}
    checks += 1
    write("UPDATE schema_migrations SET dirty=true", fixture)
    assert "migration_dirty" in probe.summarize(observe(), 2026092801)["blockers"]
    checks += 1
    write("INSERT INTO schema_migrations VALUES (2026092801,false)", fixture)
    assert "recognized_migration_tracking_required" in probe.summarize(observe(), 2026092801)["blockers"]
    checks += 1
    write("DROP TABLE schema_migrations", fixture)
    assert "recognized_migration_tracking_required" in probe.summarize(observe(), 2026092801)["blockers"]
    checks += 1
finally:
    # Remove only the unique database created by this localhost test.
    write(f"DROP DATABASE {database}", env)
print(f"uat_upgrade_readiness_postgres17_test: {checks} checks passed (fixture, not UAT acceptance)")
