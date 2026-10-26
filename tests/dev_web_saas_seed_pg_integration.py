import subprocess, tempfile, pathlib, os, socket, json, importlib.util
# Opt-in isolated integration test; caller supplies fixed-source build and SQL directory.
B=os.environ['PG17_BIN'].rstrip('/')+'/'
MIGRATECTL=os.environ['SEED_MIGRATECTL']
SOURCE=pathlib.Path(os.environ['SEED_ACCOUNTS_SOURCE'])
root=pathlib.Path(tempfile.mkdtemp(prefix='dev-seed-pg-')); os.chmod(root,0o700)
s=socket.socket(); s.bind(('127.0.0.1',0)); port=s.getsockname()[1]; s.close()
def run(args,**kw): return subprocess.run(args,check=True,capture_output=True,text=True,**kw)
def sql(q): return run([B+'psql','-XAt','-h',str(root),'-p',str(port),'-d','postgres','-v','ON_ERROR_STOP=1'],input=q)
try:
 run([B+'initdb','-D',str(root/'data'),'--auth-local=trust','--auth-host=scram-sha-256'])
 run([B+'pg_ctl','-D',str(root/'data'),'-l',str(root/'log'),'-o',f'-k {root} -p {port} -h 127.0.0.1','start'])
 sql('CREATE ROLE xc_schema_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; CREATE DATABASE account OWNER xc_schema_owner;')
 env=dict(os.environ,NATIVE_TEST_DSN=f'postgresql://xc_schema_owner@/account?host={root}&port={port}')
 args=[MIGRATECTL,'init','--environment','sit','--dsn-env','NATIVE_TEST_DSN','--writers-paused','--dry-run=false','--schema-sha256','e223518d156308c1cb2ea6919cfdccf1c0cacc03b55f9b41824ef29e4f632b13']
 first=run(args,env=env)
 spec=importlib.util.spec_from_file_location('contracts',str(pathlib.Path.cwd()/'roles/platform/dev_web_saas_seed/files/contracts.py')); c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
 source=SOURCE/'sql/init'
 billing=run(['gh','api','repos/ai-workspace-services/billing-service/contents/sql/billing-service-schema.sql?ref='+c.BILLING_SHA,'--jq','.content']).stdout
 import base64, secrets
 billing_bytes=base64.b64decode(billing)
 tables=c.verify_sources(json.loads((source/'accounts-native.manifest.json').read_text()),(source/'accounts-native.sql').read_bytes(),billing_bytes)
 def account_sql(q,user='xc_schema_owner',password=None):
  e=dict(os.environ)
  if password: e['PGPASSWORD']=password
  return run([B+'psql','-XAt','-h','127.0.0.1' if password else str(root),'-p',str(port),'-U',user,'-d','account','-v','ON_ERROR_STOP=1'],input=q,env=e)
 account_sql(billing_bytes.decode())
 passwords={r:secrets.token_hex(24) for r in ('xc_accounts_rw','xc_billing_rw','xc_lane_audit')}
 sql(''.join(f"CREATE ROLE {r} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;" for r,pw in passwords.items()))
 account_sql(c.grants(tables) + c.audit_grants())
 account_sql('SELECT version,dirty FROM public.dev_lane_schema_status;','xc_lane_audit',passwords['xc_lane_audit'])
 account_sql("BEGIN; INSERT INTO public.users(username,password,proxy_uuid) VALUES ('seed_probe','local_probe','00000000-0000-4000-8000-000000000001'); SELECT username FROM public.users WHERE username='seed_probe'; ROLLBACK;",'xc_accounts_rw',passwords['xc_accounts_rw'])
 account_sql("BEGIN; INSERT INTO public.billing_source_sync_state(source_id) VALUES ('seed_probe'); UPDATE public.billing_source_sync_state SET last_error='' WHERE source_id='seed_probe'; SELECT source_id FROM public.billing_source_sync_state WHERE source_id='seed_probe'; ROLLBACK;",'xc_billing_rw',passwords['xc_billing_rw'])
 try: account_sql('SELECT 1;','xc_accounts_rw',secrets.token_hex(24))
 except subprocess.CalledProcessError: pass
 else: raise AssertionError('foreign credential accepted')
 for query in ('INSERT INTO public.billing_source_sync_state(source_id) VALUES (NULL);','UPDATE public.schema_migrations SET dirty=true;','SET ROLE xc_accounts_rw;','SELECT password FROM public.users LIMIT 1;'):
  try: account_sql(query,'xc_lane_audit',passwords['xc_lane_audit'])
  except subprocess.CalledProcessError: pass
  else: raise AssertionError('audit privilege rejection missing')
 for role,pw in passwords.items():
  for query in ('CREATE TABLE public.__probe(id int);','CREATE TEMP TABLE __probe(id int);','SET ROLE xc_schema_owner;'):
   try: account_sql(query,role,pw)
   except subprocess.CalledProcessError: pass
   else: raise AssertionError('privilege rejection missing')
 try: account_sql('SELECT password FROM public.users LIMIT 1;','xc_billing_rw',passwords['xc_billing_rw'])
 except subprocess.CalledProcessError: pass
 else: raise AssertionError('Billing sensitive read allowed')
 again=subprocess.run(args,env=env,capture_output=True,text=True)
 assert again.returncode!=0
 print(json.dumps({'isolated_postgresql':run([B+'postgres','--version']).stdout.strip(),'actual_fixed_source_native_init':'PASS','fixed_billing_sql':'PASS','accounts_authenticated_dml':'PASS','billing_authenticated_dml':'PASS','foreign_password_refused':'PASS','cross_namespace_runtime':'NOT_RUN','audit_scram_metadata_read':'PASS','audit_dml_ddl_temp_setrole_sensitive_read_denied':'PASS','both_roles_ddl_temp_setrole_denied':'PASS','billing_users_password_read_denied':'PASS','nonempty_reinit_refused':'PASS','target':'local temporary account database','runtime_k3s':'NOT_RUN'}))
finally:
 subprocess.run([B+'pg_ctl','-D',str(root/'data'),'stop','-m','fast'],capture_output=True)
 import shutil; shutil.rmtree(root)
