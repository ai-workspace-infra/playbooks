"""Disposable loopback source SQL and full table stream validation."""
import os,pathlib,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[3]/'scripts/data_operations'))
from full_business_contract import BUSINESS_TABLES,LEGACY_BUSINESS_TABLES
from full_business_snapshot import snapshot_sql,SnapshotValidator
if os.environ.get('PGHOST') not in ('127.0.0.1','localhost') or os.environ.get('PGPORT')!='5432':raise SystemExit('Disposable loopback required')
def query(sql,db='postgres'):
 r=subprocess.run(['psql','-XAtq','-v','ON_ERROR_STOP=1'],input=sql,env=dict(os.environ,PGDATABASE=db),text=True,capture_output=True,timeout=30)
 if r.returncode:raise SystemExit('Snapshot SQL fixture failed; private output withheld')
 return r.stdout
query('CREATE DATABASE full_business_snapshot_ci;')
try:
 tables=tuple(t for t in BUSINESS_TABLES if t in LEGACY_BUSINESS_TABLES)
 query('\n'.join('CREATE TABLE public."'+t+'" (id int PRIMARY KEY, payload text); INSERT INTO public."'+t+'" VALUES (1,\'private-fixture\');' for t in tables),'full_business_snapshot_ci')
 output=query(snapshot_sql(tables),'full_business_snapshot_ci');validator=SnapshotValidator(tables)
 for line in output.splitlines():validator.accept(line)
 if validator.finish()!={t:1 for t in tables}:raise SystemExit('Full snapshot row coverage was not verified')
 print('All 44 source tables covered by a complete consistent readonly JSONL stream')
finally:query('DROP DATABASE full_business_snapshot_ci;')
