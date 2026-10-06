"""Disposable loopback source SQL and full table stream validation."""
import os,pathlib,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[3]/'scripts/data_operations'))
from full_business_contract import BUSINESS_TABLES,LEGACY_BUSINESS_TABLES
from full_business_snapshot import snapshot_sql,SnapshotValidator,visibility_guard_sql
if os.environ.get('PGHOST') not in ('127.0.0.1','localhost') or os.environ.get('PGPORT')!='5432':raise SystemExit('Disposable loopback required')
def query(sql,db='postgres'):
 r=subprocess.run(['psql','-XAtq','-v','ON_ERROR_STOP=1'],input=sql,env=dict(os.environ,PGDATABASE=db),text=True,capture_output=True,timeout=30)
 if r.returncode:raise SystemExit('Snapshot SQL fixture failed; private output withheld')
 return r.stdout
query("CREATE DATABASE full_business_snapshot_ci; CREATE ROLE readonly_release NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;")
try:
 tables=tuple(t for t in BUSINESS_TABLES if t in LEGACY_BUSINESS_TABLES)
 query('\n'.join('CREATE TABLE public."'+t+'" (id int PRIMARY KEY, payload text); INSERT INTO public."'+t+'" VALUES (1,\'private-fixture\');' for t in tables),'full_business_snapshot_ci')
 query('GRANT USAGE ON SCHEMA public TO readonly_release; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_release; ALTER TABLE users ENABLE ROW LEVEL SECURITY; CREATE POLICY release_initialization_readonly ON users FOR SELECT TO readonly_release USING(true);','full_business_snapshot_ci')
 guard=query('SET ROLE readonly_release; '+visibility_guard_sql(tables),'full_business_snapshot_ci').strip()
 if guard!='t':raise SystemExit('Complete readonly RLS visibility guard failed')
 query('CREATE POLICY restrictive_fixture ON users AS RESTRICTIVE FOR SELECT TO readonly_release USING(false);','full_business_snapshot_ci')
 if query('SET ROLE readonly_release; '+visibility_guard_sql(tables),'full_business_snapshot_ci').strip()!='f':raise SystemExit('Restrictive RLS policy was incorrectly accepted')
 query('DROP POLICY restrictive_fixture ON users;','full_business_snapshot_ci')
 output=query(snapshot_sql(tables),'full_business_snapshot_ci');validator=SnapshotValidator(tables)
 for line in output.splitlines():validator.accept(line)
 if validator.finish()!={t:1 for t in tables}:raise SystemExit('Full snapshot row coverage was not verified')
 print('All 44 source tables covered by a complete consistent readonly JSONL stream')
finally:query('DROP DATABASE full_business_snapshot_ci; DROP ROLE readonly_release;')
