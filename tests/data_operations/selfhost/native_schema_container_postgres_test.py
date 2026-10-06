#!/usr/bin/env python3
"""GH-hosted synthetic PG17 qualification of the production stdin bootstrap.

Uses the same tool_argv / TARGET_BOOTSTRAP and fixed Accounts tool. Only the
disposable container network and auto-removal are adjusted for inspection.
This is not a deployment artifact, source connection or production acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/data_operations/selfhost'))
import native_init_host as HOST

PHASE="preflight"


def require(value):
    if not value:raise RuntimeError('Disposable native schema qualification guard failed')


def run(argv,expected=0,**kwargs):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=180,**kwargs)
    require(result.returncode==expected)
    return result.stdout.strip()


def sql(query,database='postgres'):
    return run(['psql','-XAtq','-v','ON_ERROR_STOP=1','-d',database,'-c',query])


def main():
    global PHASE
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
    require(os.environ.get('PGHOST')=='127.0.0.1' and os.environ.get('PGPORT')=='5432' and os.environ.get('PGUSER')=='postgres')
    accounts=Path(os.environ['ACCOUNTS_CHECKOUT']);billing=Path(os.environ['BILLING_CHECKOUT'])
    require(run(['git','-C',str(accounts),'rev-parse','HEAD'])=='ac3239a6ddb89fd49c2b15416bf5f6ea588c6797')
    require(run(['git','-C',str(billing),'rev-parse','HEAD'])=='5b7285bf49af12983027f7624d196ab3f2b1804f')
    binary=Path(os.environ['MIGRATECTL_BIN']);require(binary.is_file())
    container=os.environ['TEST_POSTGRES_CONTAINER']
    require(container and run(['docker','inspect','-f','{{.State.Running}}',container])=='true')
    require(sql('SHOW server_version_num').startswith('17'))
    require(sql("SELECT count(*) FROM pg_database WHERE datname='account'")=='0')
    manifest=json.loads(run([str(binary),'native-schema']))
    require(manifest['schema_sha256']=='842cef3beb98ef819dc854ecdf5f85683233641a0cd85a9156b30ad59f7e0206')
    migration=billing/'sql/migrations/2026100701_cloud_vendor_costs.up.sql'
    billing_hash=hashlib.sha256(migration.read_bytes()).hexdigest()
    require(billing_hash=='a7133f3ef2ea9013a055cfd1442a7488d2b837f289e0f5d9b61624d4fde9bc53')
    target='postgresql://postgres:postgres@127.0.0.1:5432/account?sslmode=disable'
    image='native-schema-stdin-fixture:'+uuid.uuid4().hex
    execution='native-schema-stdin-check-'+uuid.uuid4().hex
    try:
        PHASE="create_fixture"
        run(['createdb','--template=template0','account'])
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);(work/'migratectl').write_bytes(binary.read_bytes());(work/'migratectl').chmod(0o755)
            (work/'Dockerfile').write_text('FROM ubuntu:24.04\nCOPY migratectl /usr/local/bin/migratectl\n')
            PHASE="build_fixture"
            run(['docker','build','-t',image,str(work)])
            migrations=work/'migrations';migrations.mkdir(mode=0o700)
            (migrations/migration.name).write_bytes(migration.read_bytes())
            def invoke(args,mounted=None,expected=0):
                argv=HOST.tool_argv(image,execution,args,mounted)
                argv.remove('--rm')
                argv[argv.index('--network')+1]='container:'+container
                try:
                    output=run(argv,expected=expected,input=target+'\n')
                    config=json.loads(run(['docker','inspect',execution]))[0]['Config']
                    encoded=json.dumps(config)
                    require(target not in encoded and 'postgresql://' not in encoded)
                    require(not any(item.startswith('NATIVE_TARGET_DSN=') for item in (config.get('Env') or [])))
                    require('--env-file' not in config['Cmd'] and '--env' not in config['Cmd'])
                    return output
                finally:
                    run(['docker','rm','--force',execution])
            init_args=['init','--dsn-env=NATIVE_TARGET_DSN','--environment=prod','--writers-paused',
                       '--schema-sha256='+manifest['schema_sha256'],'--lock-timeout=15s','--statement-timeout=5m']
            PHASE="init_preview"
            preview=json.loads(invoke([*init_args,'--dry-run=true']))
            require(preview['result']=='eligible' and preview['database_cutover_approved'] is False)
            require(sql("SELECT count(*) FROM pg_tables WHERE schemaname='public'",'account')=='0')
            PHASE="init_apply"
            applied=json.loads(invoke([*init_args,'--dry-run=false']))
            require(applied['result']=='initialized' and applied['business_rows']==0 and applied['database_cutover_approved'] is False)
            require(sql("SELECT version::text||':'||dirty::text FROM public.schema_migrations",'account')=='2026100601:false')
            upgrade=['--dir=/reviewed-migrations','migrate','--dsn-env=NATIVE_TARGET_DSN',
                     '--expected-version=2026100601','--target-version=2026100701',
                     '--migration-sha256='+billing_hash,'--lock-timeout=15s','--statement-timeout=5m']
            PHASE="billing_upgrade"
            invoke(upgrade,migrations)
            invoke(upgrade,migrations)
            require(sql("SELECT version::text||':'||dirty::text FROM public.schema_migrations",'account')=='2026100701:false')
            PHASE="scope_and_zero_rows"
            tables=sorted(manifest['business_tables']+['cloud_vendor_costs'])
            actual=json.loads(sql("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_migrations'",'account'))
            require(actual==tables)
            require(sql('SELECT '+'+'.join('(SELECT count(*) FROM public."'+t+'")' for t in tables),'account')=='0')
            bad=[arg if not arg.startswith('--migration-sha256=') else '--migration-sha256='+'0'*64 for arg in upgrade]
            PHASE="wrong_hash_refusal"
            invoke(bad,migrations,expected=1)
            require(sql("SELECT version::text||':'||dirty::text FROM public.schema_migrations",'account')=='2026100701:false')
        print('Disposable PG17/container stdin: native init preview/apply, bounded Billing upgrade/replay refusal, exact 53-table zero-row scope and secret-free Docker config passed; not production acceptance.')
    finally:
        subprocess.run(['docker','rm','--force',execution],capture_output=True)
        subprocess.run(['docker','image','rm','--force',image],capture_output=True)
        subprocess.run(['dropdb','--if-exists','account'],capture_output=True)


if __name__=='__main__':
    try:main()
    except Exception:
        print('Disposable native schema container fixture failed in '+PHASE+'; private output withheld.',file=sys.stderr)
        raise SystemExit(1)
