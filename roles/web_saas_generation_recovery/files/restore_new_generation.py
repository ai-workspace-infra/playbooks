#!/usr/bin/env python3
"""Restore an immutable archive only into a new, isolated recovery generation."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys


def validate_contract(generation, source, digest, version, counts, hostname):
    if not re.fullmatch(r"prod-v[1-9][0-9]*", generation):
        raise ValueError("invalid target generation")
    if not re.fullmatch(r"prod-v[1-9][0-9]*", source) or source == generation:
        raise ValueError("source must be a separate generation")
    if hostname != "web-saas-" + generation:
        raise ValueError("actual host is not the declared recovery generation")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("invalid archive or exact version")
    if set(counts) != {"users", "subscription_records", "billing_ledger"}:
        raise ValueError("explicit user, subscription and ledger counts are required")
    if any(type(value) is not int or value < 1 for value in counts.values()):
        raise ValueError("nonempty reviewed samples are required")


def run(args, *, data=None, source=None):
    return subprocess.run(args, input=data, stdin=source, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout.decode().strip()


def pg(sql, database="postgres"):
    return run(["docker", "exec", "-i", "web-saas-postgresql", "psql", "-U", "postgres",
                "-d", database, "-XAtq", "-v", "ON_ERROR_STOP=1"], data=sql.encode())


def database_identity():
    oid = pg("SELECT oid FROM pg_database WHERE datname='account'")
    system = pg("SELECT system_identifier FROM pg_control_system()")
    return {"database_oid": oid, "system_identifier": system}


def archive_creates_public(toc):
    return re.search(r"^\d+; \d+ \d+ SCHEMA - public(?: |$)", toc, re.MULTILINE) is not None


def state(counts):
    row = pg("SELECT version::text || ':' || dirty::text FROM public.schema_migrations",
             "account")
    if not re.fullmatch(r"[1-9][0-9]*:false", row):
        raise ValueError("migration checkpoint is not exact and clean")
    actual = {table: int(pg("SELECT count(*) FROM public." + table, "account"))
              for table in counts}
    if actual != counts:
        raise ValueError("restored business samples differ from the reviewed snapshot")
    return {"schema_version": row.split(":")[0], "dirty": False, "counts": actual}


def data_fingerprint():
    commands = pg("""
SELECT statement FROM (
  SELECT format('SELECT %L; SELECT to_jsonb(t)::text FROM %I.%I t ORDER BY (to_jsonb(t)::text) COLLATE "C";',
    'table:public.' || c.relname, 'public', c.relname) AS statement
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','p','m')
  UNION ALL
  SELECT format('SELECT %L || last_value::text || %L || is_called::text FROM %I.%I;',
    'sequence:public.' || c.relname || ':', ':', 'public', c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind='S'
) statements ORDER BY statement COLLATE "C"
""", "account")
    # Rows remain in the pipe; only the digest is retained or printed.
    payload = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
               "SET LOCAL TIMEZONE='UTC'; SET LOCAL DATESTYLE='ISO,YMD';"
               "SET LOCAL extra_float_digits=3;\n" + commands + "\nCOMMIT;\n").encode()
    proc = subprocess.Popen(["docker", "exec", "-i", "web-saas-postgresql", "psql",
                             "-U", "postgres", "-d", "account", "-XAtq", "-v", "ON_ERROR_STOP=1"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(payload)
    proc.stdin.close()
    digest = hashlib.sha256()
    while block := proc.stdout.read(65536):
        digest.update(block)
    error = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError("data fingerprint query failed")
    return digest.hexdigest()


def inspect_target(root, generation, digest):
    prepared = root / "prepared.yaml"
    if not prepared.is_file() or prepared.is_symlink():
        raise ValueError("missing new-host preparation marker")
    info = json.loads(run(["docker", "inspect", "web-saas-postgresql"]))[0]
    binds = [m for m in info["Mounts"] if m["Destination"] == "/var/lib/postgresql/data"]
    if len(binds) != 1 or binds[0]["Type"] != "bind" or binds[0]["Source"] != "/data/postgresql":
        raise ValueError("PostgreSQL is not using the declared independent data volume")
    mount = run(["findmnt", "-nro", "TARGET", "--mountpoint", "/data"])
    if mount != "/data":
        raise ValueError("independent data mount is missing")
    names = run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    if any(name.startswith("web-saas-") and name != "web-saas-postgresql" for name in names):
        # Verification with read-only apps is allowed, but initial restore is not.
        return True
    return False


def verify(marker, generation, source, digest, version, counts):
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("completed restore marker is absent")
    evidence = json.loads(marker.read_text())
    if any(evidence.get(k) != v for k, v in
           {"generation": generation, "source_generation": source,
            "archive_sha256": digest, **database_identity()}.items()):
        raise ValueError("recorded restore identity changed")
    current = state(counts)
    if current["schema_version"] != version or evidence["schema_version"] != version:
        raise ValueError("migration version mismatch")
    if data_fingerprint() != evidence["data_sha256"]:
        raise ValueError("business rows or sequences changed after restore")
    readonly = pg("SELECT rolconfig @> ARRAY['default_transaction_read_only=on'] "
                  "FROM pg_roles WHERE rolname='recovery_app'")
    if readonly != "t":
        raise ValueError("recovery application role is not read-only")
    return {**evidence, "result": "verified"}


def main():
    if len(sys.argv) != 7 or sys.argv[1] not in ("restore", "verify"):
        raise ValueError("usage: restore.py restore|verify generation source sha256 version counts")
    mode, generation, source, digest, version, raw_counts = sys.argv[1:]
    counts = json.loads(raw_counts)
    validate_contract(generation, source, digest, version, counts, socket.gethostname().split(".")[0])
    root = Path("/data/recovery") / generation
    marker = root / "restored.json"
    if not root.is_dir() or root.is_symlink() or root.resolve() != root:
        raise ValueError("recovery directory is not canonical")
    with (root / "restore.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apps_running = inspect_target(root, generation, digest)
        if mode == "verify" or marker.exists():
            print(json.dumps(verify(marker, generation, source, digest, version, counts)))
            return
        if apps_running:
            raise ValueError("application containers must not run before the first restore")
        archive = Path("/data/backups/web-saas") / generation / digest / "account.dump"
        if (not archive.is_file() or archive.is_symlink() or archive.resolve() != archive
                or stat.S_IMODE(archive.stat().st_mode) != 0o600 or archive.stat().st_uid != 0):
            raise ValueError("archive is not a private canonical root-owned file")
        actual = hashlib.sha256()
        with archive.open("rb") as fh:
            while block := fh.read(65536):
                actual.update(block)
        if actual.hexdigest() != digest:
            raise ValueError("archive checksum mismatch")
        if pg("SELECT count(*) FROM pg_database WHERE datname='account'") != "0":
            raise ValueError("account already exists without a completed checkpoint; preserve and investigate")
        values = dict(line.split("=", 1) for line in
                      Path("/etc/xcontrol/web-saas-recovery/secrets.env").read_text().splitlines()
                      if "=" in line)
        password = values["RECOVERY_APP_PASSWORD"]
        if not re.fullmatch(r"[0-9a-f]{64}", password):
            raise ValueError("invalid recovery-only credential")
        # No drop/clean command can target an existing database. The only removed
        # object is template0's empty public schema in the DB created just here.
        run(["docker", "exec", "web-saas-postgresql", "createdb", "-U", "postgres",
             "--template=template0", "account"])
        with archive.open("rb") as fh:
            toc = run(["docker", "exec", "-i", "web-saas-postgresql",
                       "pg_restore", "--list"], source=fh)
        if archive_creates_public(toc):
            pg("DROP SCHEMA public", "account")
        with archive.open("rb") as fh:
            run(["docker", "exec", "-i", "web-saas-postgresql", "pg_restore", "-U", "postgres",
                 "--dbname=account", "--no-owner", "--no-privileges",
                 "--single-transaction", "--exit-on-error"], source=fh)
        current = state(counts)
        if current["schema_version"] != version:
            raise ValueError("restored migration version differs")
        pg("CREATE ROLE recovery_app LOGIN PASSWORD '" + password + "'")
        pg("GRANT CONNECT ON DATABASE account TO recovery_app; "
           "GRANT USAGE ON SCHEMA public TO recovery_app; "
           "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO recovery_app; "
           "GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO recovery_app",
           "account")
        pg("ALTER ROLE recovery_app SET default_transaction_read_only=on")
        evidence = {"generation": generation, "source_generation": source,
                    "archive_sha256": digest, **database_identity(), **current,
                    "data_sha256": data_fingerprint(), "application_read_only": True,
                    "public_dns_cutover": False}
        # Exclusive create: a failed run preserves its database for inspection.
        with marker.open("x") as fh:
            os.chmod(marker, 0o600)
            json.dump(evidence, fh, sort_keys=True)
        print(json.dumps({**evidence, "result": "restored"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never emit SQL, connection values, subprocess stderr or row contents.
        print(json.dumps({"result": "stopped", "reason": type(exc).__name__,
                          "detail": str(exc) if isinstance(exc, ValueError) else "execution failed",
                          "target_preserved": True}), file=sys.stderr)
        sys.exit(2)
