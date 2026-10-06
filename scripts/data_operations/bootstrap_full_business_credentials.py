#!/usr/bin/env python3
"""Retryable SELECT-only bootstrap; passwords stay in runtime memory and Vault.

Persist generated credentials with Vault CAS BEFORE creating a database role.
A retry verifies/reuses the saved login, never rotates an existing identity.
Administrator connections are used only for role metadata and visibility checks.
"""
import json
import os
import secrets
import subprocess
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from full_business_contract import BUSINESS_TABLES, readonly_policy_sql, validate_source_tables

PATH = "kv/uat/database-upgrade"


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=90, **kwargs)
    if result.returncode:
        raise RuntimeError("Credential owner operation failed; private output withheld")
    return result.stdout.strip()


def vault_read(path):
    return json.loads(run(["vault", "kv", "get", "-format=json", path]))["data"]["data"]


def read_upgrade():
    # Exit 2 is ambiguous (missing path, denied permission, etc.). Use a verified
    # metadata listing to distinguish absent contract; never overwrite on error.
    result = subprocess.run(["vault", "kv", "get", "-format=json", PATH],
                            capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        data = json.loads(result.stdout)["data"]
        return data["data"], data["metadata"]["version"]
    entries = json.loads(run(["vault", "kv", "list", "-format=json", "kv/uat"]))
    if "database-upgrade" in entries or "database-upgrade/" in entries:
        raise RuntimeError("Existing credential contract cannot be read")
    return {}, 0


def save(payload, version):
    run(["vault", "kv", "put", "-cas=" + str(version), PATH, "-"], input=json.dumps(payload))
    stored, next_version = read_upgrade()
    if stored != payload or next_version != version + 1:
        raise RuntimeError("Vault CAS credential persistence was not verified")
    return next_version


def connection_env(dsn):
    p = urlsplit(dsn)
    return dict(os.environ, PGHOST=p.hostname, PGPORT=str(p.port or 5432),
                PGUSER=unquote(p.username), PGPASSWORD=unquote(p.password),
                PGDATABASE=p.path.lstrip("/"), PGSSLMODE="require", PGCONNECT_TIMEOUT="15")


def query(dsn, sql):
    return run(["psql", "-XAtq", "-v", "ON_ERROR_STOP=1"], env=connection_env(dsn), input=sql)


def count_sql(tables):
    return " UNION ALL ".join(f'SELECT \'{tab}\',count(*) FROM public."{tab}"' for tab in tables)


def readonly_dsn(p, project, password):
    return urlunsplit((p.scheme, "readonly_release." + project + ":" + quote(password, safe="") + "@" + p.hostname + ":5432", "/postgres", "sslmode=require&connect_timeout=15", ""))


def validate_saved_login(dsn, p, project):
    saved = urlsplit(dsn)
    if (saved.scheme != p.scheme or saved.hostname != p.hostname or saved.port != 5432
            or saved.path != "/postgres" or saved.username != "readonly_release." + project
            or not saved.password or len(unquote(saved.password)) < 32
            or saved.query != "sslmode=require&connect_timeout=15"):
        raise RuntimeError("Saved readonly identity differs from canonical project contract")


def main():
    if os.environ.get("CONFIRM_FULL_BUSINESS_CREDENTIAL_BOOTSTRAP") != "true":
        raise RuntimeError("Explicit full-business bootstrap confirmation required")
    contracts = {env: vault_read(f"kv/{env}/serverless/supabase") for env in ("prod", "uat")}
    if contracts["prod"]["PROJECT_REF"] == contracts["uat"]["PROJECT_REF"]:
        raise RuntimeError("PROD and UAT project identities must differ")
    payload, version = read_upgrade()
    parsed, table_sets = {}, {}
    for environment, contract in contracts.items():
        p = urlsplit(contract["DATABASE_SESSION_POOLER_URL"])
        if (p.username != "postgres." + contract["PROJECT_REF"] or p.port != 5432
                or not (p.hostname or "").endswith(".pooler.supabase.com") or p.path != "/postgres"):
            raise RuntimeError("Supabase administrator bootstrap identity is not verified")
        parsed[environment] = p
        tables = query(contract["DATABASE_SESSION_POOLER_URL"], "BEGIN READ ONLY; SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname; COMMIT;").splitlines()
        if environment == "prod":
            validate_source_tables(tables)
        table_sets[environment] = tables
        key = environment.upper() + "_SUPABASE_READONLY_DSN"
        project_key = environment.upper() + "_SUPABASE_PROJECT_REF"
        exists = query(contract["DATABASE_SESSION_POOLER_URL"], "SELECT 1 FROM pg_roles WHERE rolname='readonly_release'")
        if key in payload:
            if payload.get(project_key) != contract["PROJECT_REF"]:
                raise RuntimeError("Saved project contract differs from canonical environment")
            validate_saved_login(payload[key], p, contract["PROJECT_REF"])
        elif exists:
            raise RuntimeError("Unmanaged readonly release identity exists; implicit rotation prohibited")
        else:
            payload[key] = readonly_dsn(p, contract["PROJECT_REF"], secrets.token_urlsafe(36))
            payload[project_key] = contract["PROJECT_REF"]
    database = vault_read("kv/uat/databases")
    target = "postgresql://account_user:" + quote(database["account_pg_password"], safe="") + "@accounts-selfhost-uat.onwalk.net:5432/account?sslmode=disable"
    canonical_targets = {"UAT_SUPABASE_TARGET_DSN": contracts["uat"]["DATABASE_SESSION_POOLER_URL"], "UAT_SELFHOST_TARGET_DSN": target}
    for key, value in canonical_targets.items():
        if key in payload and payload[key] != value:
            raise RuntimeError("Existing target contract differs; implicit replacement prohibited")
        payload[key] = value
    payload.setdefault("UPGRADE_BACKUP_PASSPHRASE", secrets.token_urlsafe(48))
    if len(payload["UPGRADE_BACKUP_PASSPHRASE"]) < 32:
        raise RuntimeError("Existing backup encryption key is too short")
    payload["BOOTSTRAP_STATE"] = "pending"
    version = save(payload, version)
    counts = {}
    for environment, contract in contracts.items():
        key = environment.upper() + "_SUPABASE_READONLY_DSN"
        tables = tuple(t for t in BUSINESS_TABLES if t in table_sets[environment])
        exists = query(contract["DATABASE_SESSION_POOLER_URL"], "SELECT 1 FROM pg_roles WHERE rolname='readonly_release'")
        creation = ""
        if not exists:
            password = unquote(urlsplit(payload[key]).password)
            # Generated URL-safe passwords only; arbitrary saved SQL is rejected.
            if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in password):
                raise RuntimeError("Credential format is unsupported")
            creation = f"CREATE ROLE readonly_release LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;\n"
        else:
            # Authenticate saved identity before mutating grants.
            if query(payload[key], "SELECT current_user") != "readonly_release":
                raise RuntimeError("Saved readonly identity authentication failed")
        sql = "BEGIN;\n" + creation + """
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='readonly_release' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit)
 OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='readonly_release')
 THEN RAISE EXCEPTION 'Readonly release role has unsafe attributes or memberships'; END IF;
END $$;
ALTER ROLE readonly_release SET default_transaction_read_only=on;
GRANT CONNECT ON DATABASE postgres TO readonly_release;
GRANT USAGE ON SCHEMA public TO readonly_release;
""" + readonly_policy_sql(tables) + """
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND
 (has_table_privilege('readonly_release',c.oid,'INSERT') OR has_table_privilege('readonly_release',c.oid,'UPDATE')
 OR has_table_privilege('readonly_release',c.oid,'DELETE') OR has_table_privilege('readonly_release',c.oid,'TRUNCATE')))
 THEN RAISE EXCEPTION 'Readonly release identity has write privileges'; END IF;
END $$;
COMMIT;
"""
        query(contract["DATABASE_SESSION_POOLER_URL"], sql)
        # Owner and role counts share one consistent transaction snapshot.
        observed = query(contract["DATABASE_SESSION_POOLER_URL"], "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" + count_sql(tables) + "; SELECT 'scope_separator'; SET LOCAL ROLE readonly_release;" + count_sql(tables) + "; COMMIT;")
        owner, restricted = observed.split("scope_separator\n")
        if owner.strip() != restricted.strip():
            raise RuntimeError("Readonly release visibility differs from owner visibility")
        if query(payload[key], "SHOW default_transaction_read_only; SELECT current_user;") != "on\nreadonly_release":
            raise RuntimeError("Readonly login safety verification failed")
        counts[environment] = {name: int(count) for name, count in (line.split("|") for line in restricted.strip().splitlines())}
    payload["BOOTSTRAP_STATE"] = "ready"
    save(payload, version)
    print(json.dumps({"schema": "full-business-credential-contract/v1", "environment": "uat",
                      "source_role": "readonly_release", "projects_distinct": True,
                      "table_scope_count": len(BUSINESS_TABLES), "counts": counts,
                      "business_rows_written": False, "success": True}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        raise SystemExit("Full-business credential bootstrap failed; private runtime output withheld")
