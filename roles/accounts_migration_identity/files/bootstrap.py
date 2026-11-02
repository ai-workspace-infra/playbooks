#!/usr/bin/env python3
"""Manual owner operation. Credentials stay in Vault, stdin and Kubernetes Secrets."""
import argparse,base64,json,pathlib,re,secrets,shlex,subprocess,sys,urllib.parse
import yaml

def run(argv,body=None):
 p=subprocess.run(argv,input=body,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode: raise RuntimeError('operation failed: '+argv[0]+' (secret details suppressed)')
 return p.stdout

def main():
 a=argparse.ArgumentParser();a.add_argument('--gitops',required=True);a.add_argument('--gitops-sha',required=True);a.add_argument('--operation',required=True);a.add_argument('--host',required=True);a.add_argument('--allow-source-credential-rotation',action='store_true');a.add_argument('--phase',choices=['secrets','init','permissions'],required=True);o=a.parse_args()
 root=pathlib.Path(o.gitops).resolve();p=(root/o.operation).resolve()
 if not p.is_relative_to(root) or not re.fullmatch(r'[0-9a-f]{40}',o.gitops_sha):raise RuntimeError('invalid approved GitOps reference')
 if run(['git','-C',str(root),'rev-parse','HEAD']).strip()!=o.gitops_sha or run(['git','-C',str(root),'status','--porcelain']).strip():raise RuntimeError('GitOps must be clean at approved SHA')
 c=yaml.safe_load(p.read_text());role=c['role'];ns=c['namespace']
 if role not in ['source','target'] or c['kind']!='accounts-migration-bootstrap':raise RuntimeError('unsupported operation')
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
  material={'database_password':secrets.token_hex(32),'staging_key':base64.b64encode(secrets.token_bytes(32)).decode()}
  if role=='target':material['admin_password']=secrets.token_hex(32)
  run(['vault','kv','put',c['vault_path'],'-'],json.dumps(material))
 else:material=json.loads(vp.stdout)['data']['data']
 password=material['database_password']
 if not re.fullmatch(r'[a-f0-9]{64}',password):raise RuntimeError('unexpected managed password format')
 dbrole='readonly_release' if role=='source' else 'accounts_migration'
 dsn='postgresql://'+dbrole+':'+password+'@'+c['database_host']+':5432/account?sslmode=disable'
 if o.phase=='secrets':
  apply_secret('migration-listener',{'MIGRATION_DSN':dsn,'STAGING_KEY':material['staging_key']})
  cert=json.loads(remote(['kubectl','-n',c['tls_namespace'],'get','secret',c['tls_secret'],'-o','json']))
  apply_secret('migration-tls',{k:base64.b64decode(cert['data'][k]).decode() for k in ['tls.crt','tls.key']},'kubernetes.io/tls')
  if role=='target':
   admin=material['admin_password'];apply_secret('migration-candidate-admin',{'POSTGRES_PASSWORD':admin})
   apply_secret('migration-candidate-init',{'MIGRATION_DSN':'postgresql://postgres:'+urllib.parse.quote(admin,safe='')+'@'+c['database_host']+':5432/account?sslmode=disable'})
 elif o.phase=='init':
  if role!='target':raise RuntimeError('only candidate supports init')
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
   if sql("select to_regclass('public.schema_migrations') is not null;").strip()!='t':raise RuntimeError('native initialization missing')
  # Existing roles with elevated rights or memberships are refused, never silently broadened.
  check="SELECT count(*) FROM pg_roles WHERE rolname='"+dbrole+"' AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);"
  if sql(check).strip()!='0':raise RuntimeError('existing role has elevated privileges')
  if sql("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='"+dbrole+"');").strip()!='0':raise RuntimeError('role memberships require separate review')
  query="DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='"+dbrole+"') THEN CREATE ROLE "+dbrole+" LOGIN NOINHERIT; END IF; END $$;\n"
  query+="ALTER ROLE "+dbrole+" NOINHERIT PASSWORD '"+password+"';\nGRANT CONNECT ON DATABASE account TO "+dbrole+";\nGRANT USAGE ON SCHEMA public TO "+dbrole+";\nGRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "+dbrole+";\n"
  if role=='source':query+="ALTER ROLE readonly_release SET default_transaction_read_only=on;\n"
  else:query+="GRANT TEMP ON DATABASE account TO accounts_migration;\n"
  # Scope is explicit in the reviewed operation, never inferred from live table data.
  for table in c['tables']+['schema_migrations','accounts_controlled_migration_history','system_release_checkpoints']:
   if not re.fullmatch('[a-z_]+',table):raise RuntimeError('invalid table name')
   priv='SELECT' if role=='source' or table not in c['tables'] else 'SELECT, INSERT, UPDATE, DELETE'
   query+="DO $$ BEGIN IF to_regclass('public."+table+"') IS NOT NULL THEN GRANT "+priv+" ON public."+table+" TO "+dbrole+"; END IF; END $$;\n"
   policy='release_initialization_readonly' if role=='source' else 'migration_candidate_access'
   cmd='SELECT' if role=='source' else 'ALL'
   query+="DO $$ BEGIN IF EXISTS(SELECT FROM pg_class WHERE oid=to_regclass('public."+table+"') AND relrowsecurity) AND NOT EXISTS(SELECT FROM pg_policies WHERE schemaname='public' AND tablename='"+table+"' AND policyname='"+policy+"') THEN CREATE POLICY "+policy+" ON public."+table+" FOR "+cmd+" TO "+dbrole+" USING(true)"+('' if role=='source' else ' WITH CHECK(true)')+"; END IF; END $$;\n"
  sql('BEGIN;\n'+query+'COMMIT;')
 print(json.dumps({'role':role,'phase':o.phase,'result':'completed','gitops_sha':o.gitops_sha}))
if __name__=='__main__':
 try:main()
 except Exception as e:print(str(e),file=sys.stderr);sys.exit(1)
