#!/usr/bin/env python3
"""Disposable PostgreSQL 17 test for the historical Supabase checkpoint format."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "roles/web_saas_supabase_checkpoint_restore/files/restore_supabase_sql_gzip_verify.sh"
PG = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": os.environ.get("PGPORT", "5432"),
      "PGUSER": "postgres", "PGPASSWORD": "postgres", "PGDATABASE": "postgres",
      "PGSSLMODE": "disable", "PGCONNECT_TIMEOUT": "5"}


def psql(sql, database="postgres"):
    env = {**PG, "PGDATABASE": database}
    result = subprocess.run(["psql", "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", sql],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError("disposable PostgreSQL command failed")
    return result.stdout.strip()


def run_adapter(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=90)


def main():
    if psql("SELECT current_setting('server_version_num')::int / 10000") != "17":
        raise SystemExit("requires disposable PostgreSQL 17")
    run_id = str(uuid.uuid4().int % 900000 + 100000)
    source_id = "uat-supabase-checkpoint-fixture"
    target_id = "uat-selfhost-checkpoint-fixture"
    passphrase = "fixture-only-passphrase-not-a-secret-32"
    created = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        archive_dir = Path("/data/backups/web-saas/uat/fixture") / "daily-build-2026.10.04-r3" / run_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / "supabase_uat_daily-build-2026.10.04-r3.sql.gz.enc"
        plain = root / "checkpoint.sql"
        plain.write_text("""-- PostgreSQL database dump
-- Fixture only; no real data.
SET statement_timeout = 0;
SET search_path = public;
CREATE TABLE public.users (id integer PRIMARY KEY, password text NOT NULL);
CREATE TABLE public.subscriptions (id integer PRIMARY KEY, user_id integer NOT NULL, status text NOT NULL);
COPY public.users (id, password) FROM stdin;
1\tfixture-password
\\.
COPY public.subscriptions (id, user_id, status) FROM stdin;
1\t1\tactive
\\.
""", encoding="utf-8")
        with archive.open("wb") as output:
            compressed = subprocess.Popen(["gzip", "-c", str(plain)], stdout=subprocess.PIPE)
            encrypted = subprocess.run(
                 ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "100000",
                 "-pass", "env:WEB_SAAS_BACKUP_PASSPHRASE"],
                 env={**os.environ, "WEB_SAAS_BACKUP_PASSPHRASE": passphrase},
                stdin=compressed.stdout, stdout=output, stderr=subprocess.PIPE, text=False)
            assert compressed.stdout is not None
            compressed.stdout.close()
            compressed.wait()
            if encrypted.returncode or compressed.returncode:
                raise AssertionError("fixture archive encryption failed")
        archive.chmod(0o600)
        archive_sha = subprocess.check_output(["sha256sum", str(archive)], text=True).split()[0]
        dsn = "postgresql://postgres:postgres@127.0.0.1:5432/postgres?sslmode=disable"
        base = {**PG,
                "WEB_SAAS_ARCHIVE_FORMAT": "supabase-public-sql-gzip-aes256-cbc-v1",
                "WEB_SAAS_ARCHIVE_PATH": str(archive), "WEB_SAAS_ARCHIVE_SHA256": archive_sha,
                "WEB_SAAS_ENVIRONMENT": "uat", "WEB_SAAS_RUN_ID": run_id,
                "WEB_SAAS_SOURCE_DATABASE_ID": source_id, "WEB_SAAS_TARGET_DATABASE_ID": target_id,
                "WEB_SAAS_TARGET_HOST": "web-saas-uat", "WEB_SAAS_TARGET_DSN": dsn,
                "WEB_SAAS_BACKUP_PASSPHRASE": passphrase,
                "WEB_SAAS_SOURCE_IDENTITY_STATUS": "unavailable_from_historical_checkpoint"}
        before = psql("SELECT count(*) FROM pg_database WHERE datname LIKE 'release_verify_supabase_%'")
        restored = run_adapter(base)
        if restored.returncode:
            raise AssertionError(restored.stderr)
        receipt = json.loads(restored.stdout)
        assert receipt["restore_gate_status"] == "passed"
        assert receipt["source_identity_bound"] is False
        assert receipt["row_fidelity_bound"] is False
        assert receipt["restored_users"] == 1 and receipt["restored_subscriptions"] == 1
        target = receipt["target_database"]
        created.append(target)
        assert psql("SELECT count(*) FROM users", target) == "1"
        assert psql("SELECT count(*) FROM subscriptions", target) == "1"
        assert psql("SELECT count(*) FROM pg_database WHERE datname LIKE 'release_verify_supabase_%'") != before

        existing = "release_verify_supabase_" + str(int(run_id) + 1)
        psql(f'CREATE DATABASE "{existing}"')
        created.append(existing)
        psql("CREATE TABLE existing_only(id integer)", existing)
        occupied = run_adapter({**base, "WEB_SAAS_RUN_ID": str(int(run_id) + 1)})
        assert occupied.returncode != 0 and "already exists" in occupied.stderr

        bad_digest = run_adapter({**base, "WEB_SAAS_RUN_ID": str(int(run_id) + 2), "WEB_SAAS_ARCHIVE_SHA256": "0" * 64})
        assert bad_digest.returncode != 0 and "checksum changed" in bad_digest.stderr

        prod = run_adapter({**base, "WEB_SAAS_RUN_ID": str(int(run_id) + 3), "WEB_SAAS_ENVIRONMENT": "prod"})
        assert prod.returncode != 0 and "UAT-only" in prod.stderr
    for database in created:
        psql(f'DROP DATABASE "{database}"')
    print("uat_supabase_checkpoint_restore_postgres17_test: archive decrypt/gzip/SQL validation, isolated restore, source separation, checksum and UAT gates passed")


if __name__ == "__main__":
    main()
