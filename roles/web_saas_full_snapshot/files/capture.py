import datetime, hashlib, json, os, pathlib, subprocess, sys
os.umask(0o077)
inputs=json.load(sys.stdin)
key=inputs['key']
generation=inputs['generation']
import re, fcntl
assert re.fullmatch(r"prod-v[1-9][0-9]*", generation)
lock=open('/data/backups/web-saas/full-snapshot.lock','a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert len(key)>=32
def run(args,data=None):
    p=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise RuntimeError('snapshot command failed; inspect protected host logs')
    return p.stdout
container='web-saas-postgresql'
assert run(['hostname']).decode().strip()=='web-saas-'+generation
assert run(['findmnt','-n','-o','TARGET','-T','/data']).decode().strip()=='/data'
assert not pathlib.Path('/data/backups/web-saas').is_symlink()
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
root=pathlib.Path('/data/backups/web-saas')/generation/('full-snapshot-'+stamp[:8])/stamp
root.mkdir(parents=True,exist_ok=False,mode=0o700)
for part in ['db','runtime','vault']: (root/part).mkdir(mode=0o700)
env=dict(os.environ,SNAPSHOT_KEY=key)
original_services={x['Name']: (x['Image'],x['State']['StartedAt'],x['RestartCount']) for x in json.loads(run(['docker','inspect']+run(['docker','ps','-q']).decode().split()))}
def encrypt_stream(args,path):
    p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    with path.open('xb') as f:
        e=subprocess.run(['openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-iter','200000','-pass','env:SNAPSHOT_KEY'],stdin=p.stdout,stdout=f,stderr=subprocess.PIPE,env=env)
    p.stdout.close()
    error=p.stderr.read(); rc=p.wait()
    if rc or e.returncode: raise RuntimeError('archive pipeline failed')
def sql(db,q,name=container):
    return run(['docker','exec','-i',name,'psql','-U','postgres','-d',db,'-XAtq','-v','ON_ERROR_STOP=1'],q.encode()).decode().strip()
holder=subprocess.Popen(['docker','exec','-i',container,'psql','-U','postgres','-d','account','-XAtq','-v','ON_ERROR_STOP=1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
holder.stdin.write("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n");holder.stdin.flush()
snapshot=holder.stdout.readline().strip()
assert snapshot and all(c in '0123456789ABCDEFabcdef-' for c in snapshot)
prefix="BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '"+snapshot+"'; SET LOCAL TIMEZONE='UTC'; SET LOCAL DATESTYLE='ISO,YMD'; SET LOCAL extra_float_digits=3;\n"
tables=sql('account',prefix+"SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p') ORDER BY 1; COMMIT;").splitlines()
fingerprint_sql='\n'.join("SELECT 'table:"+t+"'; SELECT to_jsonb(t)::text FROM "+t+' t ORDER BY (to_jsonb(t)::text) COLLATE "C";' for t in tables)
def fingerprint(name,pre):
    p=subprocess.Popen(['docker','exec','-i',name,'psql','-U','postgres','-d','account','-XAtq','-v','ON_ERROR_STOP=1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    p.stdin.write((pre+fingerprint_sql+'\nCOMMIT;').encode());p.stdin.close()
    digest=hashlib.sha256()
    while True:
        chunk=p.stdout.read(1024*1024)
        if not chunk: break
        digest.update(chunk)
    if p.wait(): raise RuntimeError('fingerprint failed')
    return digest.hexdigest()
try:
    counts=json.loads(sql('account',prefix+"SELECT json_build_object('users',(SELECT count(*) FROM users),'subscriptions',(SELECT count(*) FROM subscription_records),'billing_ledger',(SELECT count(*) FROM billing_ledger),'schema',(SELECT version FROM schema_migrations LIMIT 1),'dirty',(SELECT dirty FROM schema_migrations LIMIT 1)); COMMIT;"))
    assert counts['schema']==inputs['expected_schema'] and not counts['dirty']
    source_hash=fingerprint(container,prefix)
    encrypt_stream(['docker','exec',container,'pg_dump','-U','postgres','-d','account','-Fc','--snapshot='+snapshot],root/'db/account.dump.enc')
finally:
    holder.stdin.write('ROLLBACK;\n\\q\n');holder.stdin.flush();holder.stdin.close();holder.wait(timeout=15)
encrypt_stream(['docker','exec',container,'pg_dumpall','-U','postgres','--globals-only'],root/'db/globals.sql.enc')
encrypt_stream(['tar','--numeric-owner','-czf','-','/etc/xcontrol','/opt/doco-cd'],root/'runtime/configuration.tar.gz.enc')
names=run(['docker','ps','--format','{{.Names}}']).decode().splitlines()
manifest={'snapshot_utc':stamp,'hostname':'web-saas-'+generation,'database_counts':counts,'table_count':len(tables),'source_rows_sha256':source_hash,'services':[]}
for name in names:
    item=json.loads(run(['docker','inspect',name]))[0]
    img=json.loads(run(['docker','image','inspect',item['Image']]))[0]
    manifest['services'].append({'name':name,'image':item['Config']['Image'],'image_id':item['Image'],'repo_digests':img.get('RepoDigests',[]),'started_at':item['State']['StartedAt'],'restart_count':item['RestartCount'],'mounts':[{'source':m['Source'],'destination':m['Destination'],'rw':m['RW']} for m in item['Mounts']]})
verify='snapshot-verify-'+stamp.lower()
image=json.loads(run(['docker','inspect',container]))[0]['Image']
run(['docker','run','-d','--name',verify,'--network','none','--memory','1536m','--tmpfs','/var/lib/postgresql/data:rw,size=1024m','-e','POSTGRES_HOST_AUTH_METHOD=trust',image])
import time
for attempt in range(60):
    if subprocess.run(['docker','exec',verify,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0: break
    time.sleep(1)
else: raise RuntimeError('isolated PostgreSQL unavailable; retained container')
run(['docker','exec',verify,'createdb','-U','postgres','account'])
decrypt=subprocess.Popen(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','env:SNAPSHOT_KEY','-in',str(root/'db/account.dump.enc')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
restore=subprocess.run(['docker','exec','-i',verify,'pg_restore','-U','postgres','-d','account','--no-owner','--no-acl','--exit-on-error','--single-transaction'],stdin=decrypt.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
decrypt.stdout.close()
if restore.returncode or decrypt.wait(): raise RuntimeError('isolated restore failed; retained container')
restored_hash=fingerprint(verify,"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL TIMEZONE='UTC'; SET LOCAL DATESTYLE='ISO,YMD'; SET LOCAL extra_float_digits=3;\n")
assert source_hash==restored_hash, 'restored row fingerprint mismatch; retained container'
restored_counts=json.loads(sql('account',"SELECT json_build_object('users',(SELECT count(*) FROM users),'subscriptions',(SELECT count(*) FROM subscription_records),'billing_ledger',(SELECT count(*) FROM billing_ledger),'schema',(SELECT version FROM schema_migrations LIMIT 1),'dirty',(SELECT dirty FROM schema_migrations LIMIT 1));",verify))
assert counts==restored_counts
manifest.update({'restore_status':'passed','restored_rows_sha256':restored_hash,'isolation':'network none, tmpfs, no published ports, no production bind mounts','sequence_boundary':'pg_dump observed sequence values; not MVCC transactional','vault_status':'pending'})
run(['docker','rm','-f',verify])
current_services={x['Name']: (x['Image'],x['State']['StartedAt'],x['RestartCount']) for x in json.loads(run(['docker','inspect']+run(['docker','ps','-q']).decode().split()))}
assert original_services==current_services, 'running versions changed during capture'
manifest['running_versions_unchanged']=True
(root/'runtime/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for path in root.rglob('*'):
    if path.is_file(): path.chmod(0o600)
os.sync()
print(json.dumps({'snapshot_dir':str(root),'manifest':manifest,'db_archive_sha256':hashlib.sha256((root/'db/account.dump.enc').read_bytes()).hexdigest()}))
