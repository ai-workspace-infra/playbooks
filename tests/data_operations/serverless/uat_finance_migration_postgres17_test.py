#!/usr/bin/env python3
"""Pinned official migrator against an owned disposable fixture, never real UAT."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

root = Path(__file__).resolve().parents[3]
source = Path(os.environ["ACCOUNTS_REPO_ROOT"])
sha = "c2c343fe2c91bb31e7f7f9b4fa60512a66e2b4c9"
assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == sha
migration = source / "sql/migrations/2026092801_local_finance_ledger.up.sql"
assert hashlib.sha256(migration.read_bytes()).hexdigest() == "d066e223641b4eccbb65a00dce70f717b6dce02491d1d54edc1099baf2071433"
assert os.environ.get("PGHOST") in {"127.0.0.1", "localhost"}
assert os.environ.get("PGUSER") == "postgres"
spec = importlib.util.spec_from_file_location("repair", root / "scripts/data_operations/serverless/uat_schema_repair_evidence.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def psql(sql, db="postgres"):
    env = dict(os.environ, PGDATABASE=db)
    result = subprocess.run(["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql], env=env, text=True, capture_output=True)
    if result.returncode:
        # This connection is explicitly loopback-only, with invented fixtures.
        raise RuntimeError("Disposable fixture SQL failed: " + result.stderr)
    return result.stdout.strip()


assert psql("SELECT current_setting('server_version_num')::int / 10000") == "17"
database = "uat_finance_fixture_" + uuid.uuid4().hex[:16]
created = False
try:
    psql(f'CREATE DATABASE "{database}"')
    created = True
    schema = (source / "sql/schema.sql").read_text()
    tables = []
    for name in ("users", "identities", "subscriptions"):
        match = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{name}\s*\([\s\S]*?\n\);", schema)
        assert match, f"Missing actual baseline definition: {name}"
        tables.append(match.group())
    psql("\n".join(tables), database)
    # Migration tracking is seeded ONLY in this isolated fixture, never UAT.
    psql("CREATE TABLE schema_migrations(version bigint primary key, dirty boolean not null); INSERT INTO schema_migrations VALUES(2026092703,false); INSERT INTO users(uuid,username,email,password,groups,permissions,proxy_uuid) VALUES('00000000-0000-4000-8000-000000000001','fixture','fixture@example.invalid','fixture-password','[\"old-group\"]','[\"read\"]','10000000-0000-4000-8000-000000000001'); INSERT INTO identities(user_uuid,provider,external_id) VALUES('00000000-0000-4000-8000-000000000001','fixture-provider','fixture-external'); INSERT INTO subscriptions(uuid,user_uuid,provider,payment_method,kind,external_id,status,meta) VALUES('20000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000001','fixture','fixture','subscription','fixture','active','{\"plan\":\"retained\",\"quota\":123}');", database)
    repair.query = lambda sql: json.loads(psql(sql, database))
    key = b"fixture-only"
    before = repair.capture(key)
    dsn = f"postgres://postgres:{os.environ['PGPASSWORD']}@127.0.0.1:{os.environ.get('PGPORT','5432')}/{database}?sslmode=disable"
    for _ in range(2):
        subprocess.run(["go", "run", "./cmd/migratectl", "migrate", "--dsn", dsn, "--dir", "sql/migrations"], cwd=source, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        after = repair.capture(key)
        repair.compare(before, after)
        repair.verify_finance()
    # Missing protections must not pass just because the version is clean.
    psql("ALTER TABLE finance_payments DROP CONSTRAINT finance_payments_invoice_contract_fk", database)
    try:
        repair.verify_finance()
    except repair.Blocked:
        pass
    else:
        raise AssertionError("Missing financial relationship was accepted")
    psql("UPDATE identities SET provider='changed'", database)
    try:
        repair.compare(before, repair.capture(key))
    except repair.Blocked:
        pass
    else:
        raise AssertionError("Changed identity was accepted")
    print("Pinned official Accounts migration/PostgreSQL 17 passed: exact clean version, repeat no-op, retained users/identities/nonempty subscriptions, RLS/11 triggers/FK protections; fixture only.")
finally:
    if created:
        psql(f'DROP DATABASE "{database}" WITH (FORCE)')
