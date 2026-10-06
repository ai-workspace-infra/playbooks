#!/usr/bin/env python3
"""Disposable GH-hosted PG17 and container stdin fixture, never managed DBs.

Qualifies production migration_args + CONTAINER_BOOTSTRAP with the exact fixed
Accounts binary. Loopback DSNs are permitted only by this synthetic harness;
the production source parser still requires the reviewed TLS Supabase pooler.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/data_operations/selfhost'))
import full_business_host as HOST


def require(value):
    if not value:raise RuntimeError('Disposable full-business qualification guard failed')


def run(argv,**kw):
    r=subprocess.run(argv,capture_output=True,text=True,timeout=180,**kw)
    require(r.returncode==0)
    return r.stdout.strip()


def sql(query,db='postgres'):
    return run(['psql','-XAtq','-v','ON_ERROR_STOP=1','-d',db,'-c',query])


def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
    require(os.environ.get('PGHOST')=='127.0.0.1' and os.environ.get('PGPORT')=='5432' and os.environ.get('PGUSER')=='postgres')
    account=Path(os.environ['ACCOUNTS_CHECKOUT'])
    require(run(['git','-C',str(account),'rev-parse','HEAD'])=='7b3112eb09ec1e7fbb9d35f25029818d8500980f')
    binary=Path(os.environ['MIGRATECTL_BIN']);require(binary.is_file())
    container=os.environ['TEST_POSTGRES_CONTAINER'];require(container and run(['docker','inspect','-f','{{.State.Running}}',container])=='true')
    require(sql('SHOW server_version_num').startswith('17'))
    require(sql("SELECT count(*) FROM pg_database WHERE datname IN ('account','full_business_source')")=='0')
    # Synthetic credentials exist only in private test input, never argv/config.
    source='postgresql://readonly_release:isolated-ro@127.0.0.1:5432/full_business_source?sslmode=disable'
    target='postgresql://postgres:postgres@127.0.0.1:5432/account?sslmode=disable'
    image='full-business-stdin-fixture:'+uuid.uuid4().hex
    execution='full-business-stdin-check-'+uuid.uuid4().hex
    try:
        run(['createdb','--template=template0','account'])
        run(['createdb','--template=template0','full_business_source'])
        native_sql=(account/'sql/init/accounts-native.sql').read_text()
        billing_sql=(account/'internal/migrate/testdata/cloud_vendor_costs_native.sql').read_text()
        require(__import__('hashlib').sha256(billing_sql.encode()).hexdigest()==HOST.BILLING_SQL_SHA256)
        for db in ('account','full_business_source'):
            sql(native_sql,db);sql(billing_sql,db)
            # InitializeNative establishes this control ledger separately from
            # the 52-table artifact. Both synthetic databases model the clean
            # post-init/post-Billing state; no managed database is touched.
            sql('CREATE TABLE public.schema_migrations (version bigint PRIMARY KEY, dirty boolean NOT NULL); INSERT INTO public.schema_migrations VALUES (2026100701,false)',db)
        manifest=json.loads(run([str(binary),'native-schema']))
        business=sorted(manifest['business_tables']+['cloud_vendor_costs'])
        sql("CREATE ROLE readonly_release LOGIN PASSWORD 'isolated-ro' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        sql('GRANT USAGE ON SCHEMA public TO readonly_release; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_release','full_business_source')
        for table in business:
            sql('ALTER TABLE public."'+table+'" ENABLE ROW LEVEL SECURITY; CREATE POLICY release_initialization_readonly ON public."'+table+'" FOR SELECT TO readonly_release USING(true)','full_business_source')
        sql("INSERT INTO public.users(uuid,username,password,email,proxy_uuid) VALUES('00000000-0000-0000-0000-000000000101','stdin-fixture','isolated-password','stdin@example.invalid','00000000-0000-0000-0000-000000000201')",'full_business_source')
        sql("INSERT INTO public.billing_ledger(id,account_uuid,bucket_start,bucket_end,entry_type,rated_bytes,amount_delta,balance_after) SELECT md5('ledger-'||g::text)::uuid,'00000000-0000-0000-0000-000000000101','2026-10-01T00:00:00Z'::timestamptz+g*interval '1 minute','2026-10-01T00:01:00Z'::timestamptz+g*interval '1 minute','usage',9007199254740993,0.125,12.75 FROM generate_series(1,1003)g",'full_business_source')
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'migratectl').write_bytes(binary.read_bytes());(work/'migratectl').chmod(0o755)
            # CI fixture only. Production owner always pulls the reviewed
            # full-SHA/digest image and contains no image build command.
            (work/'Dockerfile').write_text('FROM ubuntu:24.04\nCOPY migratectl /usr/local/bin/migratectl\n')
            run(['docker','build','-t',image,str(work)])
        spec={'transfer':dict(schema_sha256=HOST.ACCOUNTS_SQL_SHA256,billing_schema_sha256=HOST.BILLING_SQL_SHA256)}
        for mode in ('preview','copy','compare'):
            try:
                data=run(['docker','run','--name',execution,'-i','--network','container:'+container,'--read-only',
                    '--cap-drop','ALL','--security-opt','no-new-privileges','--entrypoint','/bin/sh',image,
                    '-c',HOST.CONTAINER_BOOTSTRAP,'--',*HOST.migration_args(spec,mode)],input=source+'\n'+target+'\n')
                config=json.loads(run(['docker','inspect',execution]))[0]['Config']
                # Docker's on-disk create config must contain no DSNs/passwords;
                # the shell's exported values exist only in the process memory.
                encoded=json.dumps(config)
                require(source not in encoded and target not in encoded and 'isolated-ro' not in encoded)
                receipt=json.loads(data)
                require(receipt['result']=={'preview':'eligible','copy':'copied','compare':'equal'}[mode])
                require(receipt['target_writes']==(mode=='copy') and receipt['full_business_equal']==(mode!='preview'))
                require(receipt['database_cutover_approved'] is False)
                if mode!='preview':require(len(receipt['tables'])==53 and receipt['tables']['billing_ledger']['rows']==1003)
                if mode=='preview':require(sql('SELECT count(*) FROM public.users','account')=='0')
            finally:
                run(['docker','rm','--force',execution])
        require(sql('SELECT min(rated_bytes) FROM public.billing_ledger','account')=='9007199254740993')
        print('Disposable PG17/container stdin: preview, 53-table copy/equality, batched exact integers and secret-free Docker config passed; not production acceptance.')
    finally:
        subprocess.run(['docker','rm','--force',execution],capture_output=True)
        subprocess.run(['docker','image','rm','--force',image],capture_output=True)
        for db in ('account','full_business_source'):subprocess.run(['dropdb','--if-exists',db],capture_output=True)


if __name__=='__main__':
    try:main()
    except Exception:
        print('Disposable full-business fixture failed; synthetic connection output withheld.',file=sys.stderr)
        raise SystemExit(1)
