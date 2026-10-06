#!/usr/bin/env python3
"""Explicit operator bootstrap: PROD SELECT-only role, UAT connection in Vault.

Run only with authorization to provision the migration identity. Never imports
data or changes application rows. Vault and database errors are withheld because
they may contain credential material. Passwords remain in process memory.
"""
import json
import os
import secrets
import subprocess
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def run(argv, *, env=None, input=None):
    result = subprocess.run(argv, env=env, input=input, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Credential bootstrap failed; sensitive runtime output withheld")
    return result.stdout


def vault_read(path):
    return json.loads(run(["vault", "kv", "get", "-format=json", path]))["data"]["data"]


def pg_env(dsn):
    p = urlsplit(dsn)
    return dict(os.environ, PGHOST=p.hostname, PGPORT=str(p.port or 5432),
                PGUSER=unquote(p.username), PGPASSWORD=unquote(p.password),
                PGDATABASE=p.path.lstrip("/"), PGCONNECT_TIMEOUT="15", PGSSLMODE="require")


def main():
    if os.environ.get("CONFIRM_UAT_IMPORT_CREDENTIAL_BOOTSTRAP") != "true":
        raise SystemExit("Explicit credential bootstrap confirmation required")
    prod = vault_read("kv/prod/serverless/supabase")
    current = vault_read("kv/uat/accounts-migration")
    uat = vault_read("kv/uat/databases")
    project = prod["PROJECT_REF"]
    admin = prod["DATABASE_SESSION_POOLER_URL"]
    p = urlsplit(admin)
    if p.username != "postgres." + project or not p.hostname.endswith(".supabase.com"):
        raise SystemExit("PROD Supabase administrator identity is not verified")
    env = pg_env(admin)
    # RLS receives a policy scoped to this dedicated SELECT-only identity.
    state = run(["psql", "-XAtq", "-v", "ON_ERROR_STOP=1", "-c",
                 "SELECT count(*),bool_or(relrowsecurity) FROM pg_class WHERE oid IN "
                 "('public.users'::regclass,'public.identities'::regclass,'public.sessions'::regclass)"], env=env).strip()
    if state not in {"3|f", "3|t"}:
        raise SystemExit("Source export tables do not match the reviewed contract")
    existing = run(["psql", "-XAtq", "-c", "SELECT 1 FROM pg_roles WHERE rolname='readonly'"], env=env).strip()
    if existing:
        raise SystemExit("Readonly role already exists; do not rotate it implicitly")
    password = secrets.token_urlsafe(36)
    sql = f"""BEGIN;
CREATE ROLE readonly LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE readonly SET default_transaction_read_only=on;
GRANT CONNECT ON DATABASE postgres TO readonly;
GRANT USAGE ON SCHEMA public TO readonly;
GRANT SELECT ON public.users,public.identities,public.sessions TO readonly;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['users','identities','sessions'] LOOP
  IF (SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.' || tab)) THEN
   EXECUTE format('CREATE POLICY accounts_migration_readonly_select ON public.%I FOR SELECT TO readonly USING (true)', tab);
  END IF;
 END LOOP;
END $$;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relkind IN ('r','p') AND
  (has_table_privilege('readonly',c.oid,'INSERT') OR has_table_privilege('readonly',c.oid,'UPDATE') OR
   has_table_privilege('readonly',c.oid,'DELETE') OR has_table_privilege('readonly',c.oid,'TRUNCATE')))
 THEN RAISE EXCEPTION 'Readonly role has write privileges'; END IF;
END $$;
COMMIT;
"""
    run(["psql", "-Xq", "-v", "ON_ERROR_STOP=1"], env=env, input=sql)
    source = urlunsplit((p.scheme, "readonly." + project + ":" + quote(password, safe="") + "@" + p.hostname + ":" + str(p.port or 5432), p.path, p.query, ""))
    count = run(["psql", "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", "SELECT count(*) FROM public.users"], env=pg_env(source)).strip()
    if not count.isdigit() or int(count) == 0:
        raise SystemExit("Readonly source returned no users; Vault was not updated")
    target = "postgresql://account_user:" + quote(uat["account_pg_password"], safe="") + "@accounts-selfhost-uat.onwalk.net:5432/account?sslmode=disable"
    # Merge preserves existing SSH fields. Vault records a new version atomically.
    current.update(MIGRATION_SOURCE_DSN=source, MIGRATION_TARGET_DSN=target,
                   MIGRATION_SOURCE_PROJECT_REF=project)
    run(["vault", "kv", "put", "kv/uat/accounts-migration", "-"], input=json.dumps(current))
    print("Credential bootstrap complete: source=verified PROD readonly; target=UAT account_user via reviewed SSH tunnel")
    print("Source readable users:", count)


if __name__ == "__main__":
    main()
