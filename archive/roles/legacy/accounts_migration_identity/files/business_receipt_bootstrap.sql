-- Target-only, owner-operated, transactional receipt bootstrap. Caller must
-- independently bind the target physical identity and verify a recoverable
-- backup before executing. This script must never run on the source database.
BEGIN;
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='60s';
DO $guard$
DECLARE executor_oid oid;
BEGIN
 IF current_setting('accounts_migration_guard.system_identifier',true) IS DISTINCT FROM (SELECT system_identifier::text FROM pg_control_system())
 OR current_setting('accounts_migration_guard.database_oid',true) IS DISTINCT FROM (SELECT oid::text FROM pg_database WHERE datname=current_database()) THEN
   RAISE EXCEPTION 'target physical identity not bound';
 END IF;
 SELECT oid INTO executor_oid FROM pg_roles WHERE rolname='accounts_migration' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
 IF executor_oid IS NULL OR executor_oid=(SELECT oid FROM pg_roles WHERE rolname=current_user)
 OR EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid=executor_oid OR member=executor_oid)
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relowner=executor_oid) THEN
   RAISE EXCEPTION 'migration login absent, privileged, assumable, or owns business tables';
 END IF;
 IF to_regnamespace('accounts_migration_api') IS NOT NULL THEN
   RAISE EXCEPTION 'receipt schema already exists; inspect before retry';
 END IF;
END
$guard$;
CREATE SCHEMA accounts_migration_api AUTHORIZATION CURRENT_USER;
REVOKE ALL ON SCHEMA accounts_migration_api FROM PUBLIC;
CREATE TABLE accounts_migration_api.receipts (
 plan_id uuid PRIMARY KEY, plan_sha256 text NOT NULL, subject text NOT NULL,
 receipt jsonb NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE accounts_migration_api.catchup_receipts (
 request_id uuid PRIMARY KEY, report_sha256 text NOT NULL, subject text NOT NULL,
 receipt jsonb NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());
REVOKE ALL ON ALL TABLES IN SCHEMA accounts_migration_api FROM PUBLIC;
GRANT USAGE ON SCHEMA accounts_migration_api TO accounts_migration;
GRANT SELECT, INSERT ON accounts_migration_api.receipts, accounts_migration_api.catchup_receipts TO accounts_migration;
DO $verify$
DECLARE owner_oid oid:=(SELECT oid FROM pg_roles WHERE rolname=current_user);
DECLARE executor_oid oid:=(SELECT oid FROM pg_roles WHERE rolname='accounts_migration');
BEGIN
 IF EXISTS (SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a WHERE n.nspname='accounts_migration_api' AND a.grantee NOT IN (owner_oid,executor_oid))
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE n.nspname='accounts_migration_api' AND c.relkind IN ('r','p') AND (a.grantee NOT IN (owner_oid,executor_oid) OR (a.grantee=executor_oid AND a.privilege_type NOT IN ('SELECT','INSERT'))))
 OR NOT has_schema_privilege('accounts_migration','accounts_migration_api','USAGE')
 OR has_schema_privilege('accounts_migration','accounts_migration_api','CREATE')
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounts_migration_api' AND c.relkind='r' AND (NOT has_table_privilege('accounts_migration',c.oid,'SELECT') OR NOT has_table_privilege('accounts_migration',c.oid,'INSERT') OR has_table_privilege('accounts_migration',c.oid,'UPDATE') OR has_table_privilege('accounts_migration',c.oid,'DELETE') OR has_table_privilege('accounts_migration',c.oid,'TRUNCATE'))) THEN
   RAISE EXCEPTION 'receipt ownership or permissions differ';
 END IF;
END
$verify$;
COMMIT;
