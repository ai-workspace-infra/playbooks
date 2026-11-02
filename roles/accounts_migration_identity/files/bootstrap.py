#!/usr/bin/env python3
"""Manual owner operation. Credentials stay in Vault, stdin and Kubernetes Secrets."""
import argparse,base64,ipaddress,json,pathlib,re,secrets,shlex,subprocess,sys,urllib.parse
import yaml

def run(argv,body=None):
 p=subprocess.run(argv,input=body,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode: raise RuntimeError('operation failed: '+argv[0]+' (secret details suppressed)')
 return p.stdout

def target_options(c):
 mode=c.get('target_mode','candidate')
 if mode not in ('candidate','business') or (mode=='business' and c.get('role')!='target'):raise RuntimeError('invalid target mode')
 secret=c.get('listener_secret_name','migration-listener')
 if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',secret) or len(secret)>253:raise RuntimeError('invalid listener Secret name')
 if mode=='business':
  allowed={'schema_version','kind','role','target_mode','schema_mode','listener_secret_name','staging_key_vault_path','namespace','vault_path','source_namespace','postgres_pod','postgres_admin','database_host','tls_namespace','tls_secret','native_job_path','tables'}
  if set(c)!=allowed or type(c.get('schema_version')) is not int or c['schema_version']!=1 or c.get('native_job_path') is not None:raise RuntimeError('business target declaration fields differ from schema')
  vault_path=c.get('vault_path')
  if not isinstance(vault_path,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*',vault_path) or '..' in vault_path.split('/') or '//' in vault_path:raise RuntimeError('invalid target Vault path')
  path=c.get('staging_key_vault_path')
  if secret=='migration-listener' or not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*',path) or '..' in path.split('/') or '//' in path or path==c['vault_path']:raise RuntimeError('business mode requires a separate staged Secret and existing staging-key path')
  host=c.get('database_host')
  if not isinstance(host,str) or len(host)>253 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]',host) or '..' in host:raise RuntimeError('invalid business database host')
  if c.get('schema_mode')!='unversioned_verified_scope':raise RuntimeError('business mode requires explicit unversioned target scope')
  tables=c.get('tables')
  if not isinstance(tables,list) or len(tables)!=42 or len(set(tables))!=42 or any(not isinstance(t,str) or not re.fullmatch('[a-z_]+',t) for t in tables):raise RuntimeError('business target requires 42 unique explicit table names')
 return mode,secret

def verify_business_login(host,c,password):
 # Password is sent only via stdin; the service path must reject a wrong
 # password and must not report a loopback client (which may use trust auth).
 cmd=['kubectl','-n',c['source_namespace'],'exec','-i',c['postgres_pod'],'--','sh','-c','IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql "$@"','sh','-h',c['database_host'],'-U','accounts_migration','-d','account','-X','-v','ON_ERROR_STOP=1','-Atc',"SELECT current_user||'|'||coalesce(host(inet_client_addr()),'')"]
 args=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',host,shlex.join(cmd)]
 wrong=subprocess.run(args,input=secrets.token_hex(32)+'\n',capture_output=True,text=True,timeout=20)
 if wrong.returncode==0:raise RuntimeError('database accepted an incorrect migration password')
 good=subprocess.run(args,input=password+'\n',capture_output=True,text=True,timeout=20)
 if good.returncode!=0:raise RuntimeError('managed migration credential cannot authenticate')
 parts=good.stdout.strip().split('|')
 try:
  if len(parts)!=2 or parts[0]!='accounts_migration' or ipaddress.ip_address(parts[1]).is_loopback:raise ValueError()
 except ValueError:raise RuntimeError('migration authentication was not verified over a non-loopback service connection') from None

def main():
 a=argparse.ArgumentParser();a.add_argument('--gitops',required=True);a.add_argument('--gitops-sha',required=True);a.add_argument('--operation',required=True);a.add_argument('--host',required=True);a.add_argument('--allow-source-credential-rotation',action='store_true');a.add_argument('--phase',choices=['secrets','init','permissions'],required=True);o=a.parse_args()
 root=pathlib.Path(o.gitops).resolve();p=(root/o.operation).resolve()
 if not p.is_relative_to(root) or not re.fullmatch(r'[0-9a-f]{40}',o.gitops_sha):raise RuntimeError('invalid approved GitOps reference')
 if run(['git','-C',str(root),'rev-parse','HEAD']).strip()!=o.gitops_sha or run(['git','-C',str(root),'status','--porcelain']).strip():raise RuntimeError('GitOps must be clean at approved SHA')
 c=yaml.safe_load(p.read_text());role=c['role'];ns=c['namespace']
 if role not in ['source','target'] or c['kind']!='accounts-migration-bootstrap':raise RuntimeError('unsupported operation')
 mode,listener_secret=target_options(c)
 for key in ['namespace','source_namespace','postgres_pod','postgres_admin','tls_namespace','tls_secret']:
  if not re.fullmatch(r'[a-zA-Z0-9_.-]+',c[key]):raise RuntimeError('invalid identifier')
 def remote(args,body=None):return run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',o.host,shlex.join(args)],body)
 def kube(args,body=None):return remote(['kubectl','-n',ns]+args,body)
 def apply_secret(name,data,typ='Opaque'):
  obj={'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':ns},'type':typ,'data':{k:base64.b64encode(v.encode()).decode() for k,v in data.items()}}
  kube(['apply','-f','-'],json.dumps(obj))
 def sql(query):return remote(['kubectl','-n',c['source_namespace'],'exec','-i',c['postgres_pod'],'--','psql','-U',c['postgres_admin'],'-d','account','-X','-v','ON_ERROR_STOP=1','-At'],query)
 vp=subprocess.run(['vault','kv','get','-format=json',c['vault_path']],capture_output=True,text=True)
 fresh=vp.returncode!=0
 if fresh:
  if o.phase!='secrets' or 'No value found' not in vp.stderr:raise RuntimeError('Vault material unavailable; no fallback')
  material={'database_password':secrets.token_hex(32)}
  if mode!='business':material['staging_key']=base64.b64encode(secrets.token_bytes(32)).decode()
  if role=='target' and mode!='business':material['admin_password']=secrets.token_hex(32)
  run(['vault','kv','put',c['vault_path'],'-'],json.dumps(material))
 else:material=json.loads(vp.stdout)['data']['data']
 password=material['database_password']
 if not re.fullmatch(r'[a-f0-9]{64}',password):raise RuntimeError('unexpected managed password format')
 if mode=='business':
  stage=json.loads(run(['vault','kv','get','-format=json',c['staging_key_vault_path']]))['data']['data']['staging_key']
 else:stage=material['staging_key']
 try:
  if len(base64.b64decode(stage,validate=True))!=32:raise ValueError()
 except Exception:raise RuntimeError('invalid staging key') from None
 dbrole='readonly_release' if role=='source' else 'accounts_migration'
 dsn='postgresql://'+dbrole+':'+password+'@'+c['database_host']+':5432/account?sslmode=disable'
 if o.phase=='secrets':
  apply_secret(listener_secret,{'MIGRATION_DSN':dsn,'STAGING_KEY':stage})
  if mode!='business':
   cert=json.loads(remote(['kubectl','-n',c['tls_namespace'],'get','secret',c['tls_secret'],'-o','json']))
   apply_secret('migration-tls',{k:base64.b64decode(cert['data'][k]).decode() for k in ['tls.crt','tls.key']},'kubernetes.io/tls')
  if role=='target' and mode!='business':
   admin=material['admin_password'];apply_secret('migration-candidate-admin',{'POSTGRES_PASSWORD':admin})
   apply_secret('migration-candidate-init',{'MIGRATION_DSN':'postgresql://postgres:'+urllib.parse.quote(admin,safe='')+'@'+c['database_host']+':5432/account?sslmode=disable'})
 elif o.phase=='init':
  if role!='target' or mode!='candidate':raise RuntimeError('only candidate supports init')
  kube(['rollout','status','statefulset/migration-candidate','--timeout=180s'])
  count=sql("select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE';").strip()
  job=json.loads(kube(['get','jobs','-o','json']))
  existing=next((x for x in job['items'] if x['metadata']['name']=='migration-native-init'),None)
  if existing:
   if existing.get('status',{}).get('succeeded')!=1:raise RuntimeError('existing init job incomplete; preserve and investigate')
  elif count!='0':raise RuntimeError('candidate not empty; initialization refused')
  else:
   manifest=(root/c['native_job_path']).resolve()
   if not manifest.is_relative_to(root):raise RuntimeError('invalid native job path')
   kube(['apply','-f','-'],manifest.read_text());kube(['wait','--for=condition=complete','job/migration-native-init','--timeout=360s'])
 elif o.phase=='permissions':
  if role=='source' and not o.allow_source_credential_rotation:raise RuntimeError('source credential rotation requires explicit operation approval')
  if role=='target':
   checkpoint=sql("select to_regclass('public.schema_migrations') is not null;").strip()
   if mode=='candidate' and checkpoint!='t':raise RuntimeError('native initialization missing')
   if mode=='business':
    if checkpoint!='f':raise RuntimeError('business target checkpoint state differs')
    missing=','.join("('"+table+"')" for table in c['tables'])
    if sql("SELECT count(*) FROM (VALUES "+missing+") AS t(name) WHERE to_regclass('public.'||name) IS NULL;").strip()!='0':raise RuntimeError('business target migration scope incomplete')
    hba="SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL OR (type LIKE 'host%' AND auth_method='trust' AND NOT COALESCE((address='127.0.0.1' AND netmask='255.255.255.255') OR (address='::1' AND netmask='ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'),false));"
    if sql(hba).strip()!='0':raise RuntimeError('non-loopback database trust authentication requires review')
  # Existing roles with elevated rights or memberships are refused, never silently broadened.
  check="SELECT count(*) FROM pg_roles WHERE rolname='"+dbrole+"' AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);"
  if sql(check).strip()!='0':raise RuntimeError('existing role has elevated privileges')
  if sql("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='"+dbrole+"');").strip()!='0':raise RuntimeError('role memberships require separate review')
  if mode=='business':
   if sql("SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='"+dbrole+"');").strip()!='0':raise RuntimeError('migration role can be assumed by another role')
   if sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='"+dbrole+"');").strip()!='0':raise RuntimeError('migration role owns business tables')
   if sql("SELECT CASE WHEN to_regrole('"+dbrole+"') IS NULL THEN 0 ELSE (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND (has_table_privilege('"+dbrole+"',c.oid,'DELETE') OR has_table_privilege('"+dbrole+"',c.oid,'TRUNCATE'))) END;").strip()!='0':raise RuntimeError('migration role already has destructive privileges')
   role_exists=sql("SELECT count(*) FROM pg_roles WHERE rolname='"+dbrole+"';").strip()
   if role_exists not in ('0','1'):raise RuntimeError('migration role state unavailable')
   if role_exists=='1':verify_business_login(o.host,c,password)
  query="DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='"+dbrole+"') THEN CREATE ROLE "+dbrole+" LOGIN NOINHERIT; END IF; END $$;\n"
  if mode=='business':
   query="DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='"+dbrole+"') THEN CREATE ROLE "+dbrole+" LOGIN NOINHERIT PASSWORD '"+password+"'; END IF; END $$;\n"
   query+="ALTER ROLE "+dbrole+" NOINHERIT;\n"
  else:query+="ALTER ROLE "+dbrole+" NOINHERIT PASSWORD '"+password+"';\n"
  query+="GRANT CONNECT ON DATABASE account TO "+dbrole+";\nGRANT USAGE ON SCHEMA public TO "+dbrole+";\nGRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "+dbrole+";\n"
  if role=='source':query+="ALTER ROLE readonly_release SET default_transaction_read_only=on;\n"
  else:query+="GRANT TEMP ON DATABASE account TO accounts_migration;\n"
  # Scope is explicit in the reviewed operation, never inferred from live table data.
  for table in c['tables']+['schema_migrations','accounts_controlled_migration_history','system_release_checkpoints']:
   if not re.fullmatch('[a-z_]+',table):raise RuntimeError('invalid table name')
   priv='SELECT' if role=='source' or table not in c['tables'] else ('SELECT, INSERT, UPDATE' if mode=='business' else 'SELECT, INSERT, UPDATE, DELETE')
   query+="DO $$ BEGIN IF to_regclass('public."+table+"') IS NOT NULL THEN GRANT "+priv+" ON public."+table+" TO "+dbrole+"; END IF; END $$;\n"
   policy='release_initialization_readonly' if role=='source' else 'migration_candidate_access'
   cmd='SELECT' if role=='source' else 'ALL'
   query+="DO $$ BEGIN IF EXISTS(SELECT FROM pg_class WHERE oid=to_regclass('public."+table+"') AND relrowsecurity) AND NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='public' AND tablename='"+table+"' AND policyname='"+policy+"') THEN CREATE POLICY "+policy+" ON public."+table+" FOR "+cmd+" TO "+dbrole+" USING(true)"+('' if role=='source' else ' WITH CHECK(true)')+"; END IF; END $$;\n"
   if mode=='business' and table in c['tables']:
    query+="DO $$ BEGIN IF EXISTS(SELECT FROM pg_class WHERE oid=to_regclass('public."+table+"') AND relrowsecurity) AND (NOT EXISTS (SELECT FROM pg_policy p WHERE p.polrelid=to_regclass('public."+table+"') AND p.polname='migration_candidate_access' AND p.polcmd='*' AND p.polpermissive AND p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname='accounts_migration')] AND pg_get_expr(p.polqual,p.polrelid)='true' AND pg_get_expr(p.polwithcheck,p.polrelid)='true') OR EXISTS (SELECT FROM pg_policy p WHERE p.polrelid=to_regclass('public."+table+"') AND NOT p.polpermissive AND p.polcmd IN ('r','*') AND (0::oid=ANY(p.polroles) OR (SELECT oid FROM pg_roles WHERE rolname='accounts_migration')=ANY(p.polroles)))) THEN RAISE EXCEPTION 'target RLS policy differs'; END IF; END $$;\n"
  if role=='source':
   query+="DO $$ BEGIN IF EXISTS(SELECT FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p') AND (has_table_privilege('readonly_release',c.oid,'INSERT') OR has_table_privilege('readonly_release',c.oid,'UPDATE') OR has_table_privilege('readonly_release',c.oid,'DELETE') OR has_table_privilege('readonly_release',c.oid,'TRUNCATE'))) THEN RAISE EXCEPTION 'source role has write privileges'; END IF; END $$;\n"
  sql('BEGIN;\n'+query+'COMMIT;')
  if mode=='business':verify_business_login(o.host,c,password)
 print(json.dumps({'role':role,'phase':o.phase,'result':'completed','gitops_sha':o.gitops_sha}))
if __name__=='__main__':
 try:main()
 except Exception as e:print(str(e),file=sys.stderr);sys.exit(1)
