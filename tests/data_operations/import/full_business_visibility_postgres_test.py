"""Disposable loopback PostgreSQL only; no live credentials or row output."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/data_operations'))
import bootstrap_full_business_credentials as subject

if os.environ.get('PGHOST') not in ('127.0.0.1','localhost') or os.environ.get('PGPORT')!='5432':
    raise SystemExit('Visibility integration requires disposable loopback PostgreSQL')
env=dict(os.environ, PGDATABASE='full_business_visibility_ci')

def run(sql, database='postgres'):
    result=subprocess.run(['psql','-XAtq','-v','ON_ERROR_STOP=1'],input=sql,env=dict(env,PGDATABASE=database),capture_output=True,text=True,timeout=30)
    if result.returncode: raise SystemExit('Disposable visibility fixture failed; output withheld')

run('CREATE DATABASE full_business_visibility_ci; CREATE ROLE full_business_visibility_ci LOGIN PASSWORD \'ci-private\' NOSUPERUSER NOBYPASSRLS NOINHERIT;')
try:
    run("CREATE TABLE users (uuid int PRIMARY KEY); INSERT INTO users VALUES (1),(2); ALTER TABLE users ENABLE ROW LEVEL SECURITY; GRANT CONNECT ON DATABASE full_business_visibility_ci TO full_business_visibility_ci; GRANT USAGE ON SCHEMA public TO full_business_visibility_ci; GRANT SELECT ON public.users TO full_business_visibility_ci; CREATE POLICY visible_ci ON users FOR SELECT TO full_business_visibility_ci USING(true);",'full_business_visibility_ci')
    def connection(identity):
        return dict(env,PGUSER=env['PGUSER'] if identity=='owner' else 'full_business_visibility_ci',PGPASSWORD=env['PGPASSWORD'] if identity=='owner' else 'ci-private')
    with patch.object(subject,'connection_env',side_effect=connection):
        counts=subject.verify_visibility('owner','readonly',('users',))
        if counts!={'users':2}: raise SystemExit('Complete row visibility was not verified')
        run('DROP POLICY visible_ci ON users;','full_business_visibility_ci')
        try: subject.verify_visibility('owner','readonly',('users',))
        except RuntimeError: pass
        else: raise SystemExit('Incomplete RLS row visibility was incorrectly accepted')
    print('Separate-login consistent snapshots verified; RLS invisibility rejected')
finally:
    run('DROP DATABASE full_business_visibility_ci; DROP ROLE full_business_visibility_ci;')
