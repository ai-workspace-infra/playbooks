"""Reviewed application DML grants; schema migrations retain their own owner."""


def primary_permissions_sql():
    return """
BEGIN;
SET LOCAL lock_timeout = '5s';
SELECT pg_advisory_xact_lock(73462, 1);
DO $gate$
DECLARE r record; t record; s record;
BEGIN
  SELECT * INTO STRICT r FROM pg_roles WHERE rolname = 'generation_app';
  IF r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
    RAISE EXCEPTION 'application role has unexpected administrative privileges';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid) THEN
    RAISE EXCEPTION 'application role has unexpected memberships';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class WHERE relowner = r.oid)
     OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = r.oid)
     OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = r.oid)
     OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = r.oid) THEN
    RAISE EXCEPTION 'application role must not own schema objects';
  END IF;
  -- Preserve the native candidate's existing BYPASSRLS setting. A newly
  -- introduced policy contract requires separate review, not a broad grant.
  IF NOT r.rolbypassrls AND EXISTS (
    SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace AND relrowsecurity
  ) THEN
    RAISE EXCEPTION 'application RLS contract is not prepared';
  END IF;
  FOR t IN SELECT c.oid, c.relname FROM pg_class c
    WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
      AND c.relname <> 'schema_migrations'
      AND c.relname !~ '^(migratectl_|migration_)'
  LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO generation_app', t.relname);
  END LOOP;
  FOR s IN SELECT DISTINCT seq.relname FROM pg_class seq
    JOIN pg_depend dep ON dep.objid=seq.oid AND dep.classid='pg_class'::regclass
    JOIN pg_class tab ON tab.oid=dep.refobjid AND dep.refclassid='pg_class'::regclass
    WHERE seq.relnamespace='public'::regnamespace AND seq.relkind='S'
      AND dep.deptype IN ('a','i') AND tab.relname <> 'schema_migrations'
      AND tab.relname !~ '^(migratectl_|migration_)'
  LOOP
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE public.%I TO generation_app', s.relname);
  END LOOP;
  IF has_schema_privilege('generation_app','public','CREATE')
     OR has_database_privilege('generation_app',current_database(),'CREATE')
     OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace='public'::regnamespace
       AND c.relkind IN ('r','p') AND has_table_privilege('generation_app',c.oid,'TRUNCATE,TRIGGER,REFERENCES'))
     OR has_table_privilege('generation_app','public.schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE') THEN
    RAISE EXCEPTION 'application role has unexpected DDL or migration privileges';
  END IF;
END $gate$;
GRANT CONNECT ON DATABASE account TO generation_app;
GRANT USAGE ON SCHEMA public TO generation_app;
ALTER ROLE generation_app SET default_transaction_read_only=off;
COMMIT;
"""
