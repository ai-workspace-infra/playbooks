#!/usr/bin/env python3
"""Disposable PostgreSQL 17 proof of checkpoint, restore, bounded upgrade and rollback.

This test never contacts UAT.  It uses a local PostgreSQL service, a local
fixture-only encryption key, and the reviewed Accounts migrator source.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(os.environ["ACCOUNTS_REPO_ROOT"])
ACCOUNTS_SHA = "a83be53810e8114c11d0edf7d6dcace66241cf73"
MIGRATION_SHA = "d066e223641b4eccbb65a00dce70f717b6dce02491d1d54edc1099baf2071433"
EXPECTED = "2026092703"
TARGET = "2026092801"


def run(args, *, env=None, check=True):
    result = subprocess.run(args, text=True, capture_output=True, env=env, timeout=300)
    if check and result.returncode:
        raise RuntimeError(f"fixture command failed: {args[0]}")
    return result


def psql(database, sql, *, check=True):
    env = dict(os.environ, PGDATABASE=database)
    return run(["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql], env=env, check=check)


def create_database(database):
    psql("postgres", f'CREATE DATABASE "{database}"')


def drop_database(database):
    psql("postgres", f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', check=False)


def capture(database):
    query = """
    SELECT json_build_object(
      'users', COALESCE((SELECT json_agg(row_to_json(x) ORDER BY uuid) FROM
        (SELECT uuid, username, email, password, groups, permissions, proxy_uuid FROM public.users) x), '[]'::json),
      'identities', COALESCE((SELECT json_agg(row_to_json(x) ORDER BY user_uuid, provider, external_id) FROM
        (SELECT user_uuid, provider, external_id FROM public.identities) x), '[]'::json),
      'subscriptions', COALESCE((SELECT json_agg(row_to_json(x) ORDER BY uuid) FROM
        (SELECT uuid, user_uuid, provider, payment_method, kind, external_id, status, meta FROM public.subscriptions) x), '[]'::json)
    )::text
    """
    return json.loads(psql(database, query).stdout.strip())


def database_identity(database):
    return psql(database, "SELECT current_database() || ':' || (SELECT oid FROM pg_database WHERE datname = current_database())").stdout.strip()


def bounded(database, *, checksum=MIGRATION_SHA):
    dsn = f"postgres://postgres:{os.environ['PGPASSWORD']}@127.0.0.1:{os.environ.get('PGPORT', '5432')}/{database}?sslmode=disable"
    env = dict(os.environ, UAT_FIXTURE_DATABASE_URL=dsn)
    return run([
        "go", "run", "./cmd/migratectl", "migrate",
        "--dsn-env", "UAT_FIXTURE_DATABASE_URL", "--dir", "sql/migrations",
        "--expected-version", EXPECTED, "--target-version", TARGET,
        "--migration-sha256", checksum, "--lock-timeout", "15s",
        "--statement-timeout", "5m",
    ], env=env, check=False)


def main():
    assert run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"]).stdout.strip() == ACCOUNTS_SHA
    migration = SOURCE / "sql/migrations/2026092801_local_finance_ledger.up.sql"
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == MIGRATION_SHA

    baseline_db = "uat_upgrade_fixture_" + uuid.uuid4().hex[:12]
    restore_db = "uat_restore_fixture_" + uuid.uuid4().hex[:12]
    rollback_db = "uat_rollback_fixture_" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="uat-upgrade-proof-") as directory:
        directory = Path(directory)
        dump = directory / "baseline.dump"
        encrypted = directory / "baseline.dump.enc"
        key_env = dict(os.environ, FIXTURE_CHECKPOINT_KEY="fixture-only-postgres17-key")
        try:
            create_database(baseline_db)
            schema = (SOURCE / "sql/schema.sql").read_text()
            table_sql = []
            import re
            for name in ("users", "identities", "subscriptions"):
                match = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{name}\s*\([\s\S]*?\n\);", schema)
                assert match, f"missing baseline table {name}"
                table_sql.append(match.group())
            psql(baseline_db, "\n".join(table_sql))
            psql(baseline_db, """
                CREATE TABLE schema_migrations(version bigint primary key, dirty boolean not null);
                INSERT INTO schema_migrations VALUES (2026092703, false);
                INSERT INTO users(uuid, username, email, password, groups, permissions, proxy_uuid)
                VALUES ('00000000-0000-4000-8000-000000000001', 'fixture', 'fixture@example.invalid', 'fixture-password', '[\"old-group\"]', '[\"read\"]', '10000000-0000-4000-8000-000000000001');
                INSERT INTO identities(user_uuid, provider, external_id)
                VALUES ('00000000-0000-4000-8000-000000000001', 'fixture-provider', 'fixture-external');
                INSERT INTO subscriptions(uuid, user_uuid, provider, payment_method, kind, external_id, status, meta)
                VALUES ('20000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001', 'fixture', 'fixture', 'subscription', 'fixture', 'active', '{\"plan\":\"retained\",\"quota\":123}');
            """)
            before = capture(baseline_db)

            run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump), baseline_db], env=os.environ)
            run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:FIXTURE_CHECKPOINT_KEY", "-in", str(dump), "-out", str(encrypted)], env=key_env)
            assert encrypted.stat().st_size > 0
            checkpoint_sha = hashlib.sha256(encrypted.read_bytes()).hexdigest()
            assert checkpoint_sha == hashlib.sha256(encrypted.read_bytes()).hexdigest()

            create_database(restore_db)
            decrypted = directory / "restored.dump"
            run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:FIXTURE_CHECKPOINT_KEY", "-in", str(encrypted), "-out", str(decrypted)], env=key_env)
            run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", restore_db, str(decrypted)])
            assert capture(restore_db) == before
            assert database_identity(restore_db) != database_identity(baseline_db)

            failed = bounded(baseline_db, checksum="0" * 64)
            assert failed.returncode != 0
            assert capture(baseline_db) == before
            psql(baseline_db, "UPDATE schema_migrations SET dirty = true")
            dirty_failed = bounded(baseline_db)
            assert dirty_failed.returncode != 0
            assert psql(baseline_db, "SELECT dirty FROM schema_migrations").stdout.strip() == "t"

            # Recover the clean baseline from the encrypted checkpoint; never
            # force-clear the dirty marker in the fixture or in an environment.
            drop_database(baseline_db)
            create_database(baseline_db)
            run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", baseline_db, str(decrypted)])
            assert capture(baseline_db) == before
            assert psql(baseline_db, "SELECT dirty FROM schema_migrations").stdout.strip() == "f"

            first = bounded(baseline_db)
            assert first.returncode == 0
            upgraded = capture(baseline_db)
            assert upgraded["users"] == before["users"]
            assert upgraded["identities"] == before["identities"]
            assert upgraded["subscriptions"] == before["subscriptions"]
            assert psql(baseline_db, "SELECT version FROM schema_migrations WHERE version = 2026092801 AND dirty = false").stdout.strip() == TARGET
            assert bounded(baseline_db).returncode == 0
            assert capture(baseline_db) == upgraded

            create_database(rollback_db)
            run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", rollback_db, str(decrypted)])
            assert capture(rollback_db) == before
            assert bounded(rollback_db).returncode == 0
            assert bounded(rollback_db).returncode == 0
            assert capture(rollback_db) == upgraded
            print("PG17 fixture passed: encrypted checkpoint, isolated restore, exact bounded upgrade, checksum failure protection, rollback restore, and same-artifact re-upgrade; no UAT/PROD access.")
        finally:
            for database in (baseline_db, restore_db, rollback_db):
                drop_database(database)


if __name__ == "__main__":
    main()
