#!/usr/bin/env python3
"""SELECT-only PROD full snapshot encrypted on the approved UAT retained disk.

No business target database writes, plaintext dump files, or artifact uploads.
Every source JSONL record is counted in memory; the remote decrypt hash must
match the exact source stream before a successful preparation receipt.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from bootstrap_full_business_credentials import connection_env,query
from full_business_contract import BUSINESS_TABLES,validate_source_tables
from full_business_snapshot import SnapshotValidator,snapshot_sql,visibility_guard_sql
from mount_uat_retained_volume import disk_contract,run


REMOTE=r'''set -euo pipefail
umask 077
read -r UPGRADE_KEY
export UPGRADE_KEY
[[ ${#UPGRADE_KEY} -ge 32 ]]
[[ $(findmnt -n -o TARGET --target /data) == /data ]]
[[ $(readlink -f "$(findmnt -n -o SOURCE --target /data)") == $(readlink -f /dev/disk/by-id/google-web-saas-uat-upgrade-data) ]]
parent=/data/backups/web-saas/uat/initialization
[[ $(realpath -m "$parent") == "$parent" ]]
install -d -m 700 -o root -g root "$parent"
checkpoint="$parent/prod-business-$1"
mkdir -m 700 "$checkpoint"
archive="$checkpoint/snapshot.jsonl.enc"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:UPGRADE_KEY > "$archive"
test -s "$archive"
sync
cipher_sha=$(sha256sum "$archive" | awk '{print $1}')
plain_sha=$(openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:UPGRADE_KEY -in "$archive" | sha256sum | awk '{print $1}')
python3 - "$archive" "$cipher_sha" "$plain_sha" <<'RECEIPT'
import json,sys
print(json.dumps({'archive_path':sys.argv[1],'archive_sha256':sys.argv[2],'plaintext_sha256':sys.argv[3],'encrypted':True,'durable_mount':'/data'}))
RECEIPT
'''


def stream(source_dsn,ssh_command,key,tables):
    validator=SnapshotValidator(tables);digest=hashlib.sha256();size=0
    with tempfile.TemporaryFile() as errors:
        source=subprocess.Popen(['psql','-XAtq','-v','ON_ERROR_STOP=1'],env=connection_env(source_dsn),
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors)
        destination=subprocess.Popen(ssh_command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors)
        def terminate():
            for process in (source,destination):
                if process.poll() is None:process.kill()
        timer=threading.Timer(600,terminate);timer.start()
        try:
            destination.stdin.write((key+'\n').encode());destination.stdin.flush()
            source.stdin.write(snapshot_sql(tables).encode());source.stdin.close()
            for line in source.stdout:
                size+=len(line)
                if size>4*1024**3:raise RuntimeError('Source snapshot exceeds reviewed stream size')
                validator.accept(line)
                digest.update(line);destination.stdin.write(line)
            if source.wait(timeout=10)!=0:raise RuntimeError('Readonly source snapshot failed')
            counts=validator.finish()
            destination.stdin.close();destination.stdin=None
            output=destination.communicate(timeout=60)[0]
            if destination.returncode:raise RuntimeError('Encrypted UAT archive persistence failed')
            receipt=json.loads(output.strip())
            if receipt.get('plaintext_sha256')!=digest.hexdigest() or not receipt.get('encrypted'):
                raise RuntimeError('Persisted archive differs from source stream')
            return receipt,counts,size
        finally:
            terminate()
            for process in (source,destination):process.wait(timeout=10)
            timer.cancel()


def main():
    if os.environ.get('CONFIRM_PROD_BUSINESS_SNAPSHOT')!='true':
        raise RuntimeError('Explicit full business PROD snapshot confirmation required')
    caller=os.environ.get('CALLER_RUN_ID','')
    if not re.fullmatch(r'[1-9][0-9]*',caller):raise RuntimeError('Successful approved caller required')
    root=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='prod-business-snapshot-') as tmp:
        work=Path(tmp)
        run(['gh','run','download',caller,'--repo','ai-workspace-infra/platform-ops-toolkit','--name','platform-ops-toolkit-cmdb','--dir',tmp])
        env=dict(os.environ,REQUESTED_ENVIRONMENT='uat',CALLER_RUN_ID=caller,CONFIG_JSON=json.dumps({'target_host':'web-saas-uat'}),GH_TOKEN=run(['gh','auth','token']).strip(),CMDB_FILE=str(work/'cmdb.json'))
        run(['python3',str(root/'verify_caller_cmdb.py')],env=env)
        metadata=json.loads(run(['gh','api','repos/ai-workspace-infra/platform-ops-toolkit/actions/runs/'+caller]))
        if metadata.get('conclusion')!='success':raise RuntimeError('Caller did not complete successfully')
        host=json.loads((work/'cmdb.json').read_text())['web-saas-uat'];disk_contract(host)
        user=host.get('ansible_user','root')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user):raise RuntimeError('Invalid SSH identity')
        contract=json.loads(run(['vault','kv','get','-format=json','kv/uat/database-upgrade']))['data']['data']
        if contract.get('BOOTSTRAP_STATE')!='ready' or contract['PROD_SUPABASE_PROJECT_REF']==contract['UAT_SUPABASE_PROJECT_REF']:
            raise RuntimeError('Verified distinct full business connection contract required')
        dsn=contract['PROD_SUPABASE_READONLY_DSN'];parsed=urlsplit(dsn)
        if parsed.username!='readonly_release.'+contract['PROD_SUPABASE_PROJECT_REF'] or parsed.port!=5432 or not (parsed.hostname or '').endswith('.pooler.supabase.com'):
            raise RuntimeError('Source must be the canonical dedicated readonly login')
        if query(dsn,'SELECT current_user; SHOW default_transaction_read_only;')!='readonly_release\non':
            raise RuntimeError('Source readonly login is not verified')
        present=query(dsn,"BEGIN READ ONLY; SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p') ORDER BY relname; COMMIT;").splitlines()
        validate_source_tables(present)
        tables=tuple(t for t in BUSINESS_TABLES if t in present)
        if query(dsn,visibility_guard_sql(tables))!='t':
            raise RuntimeError('Complete readonly RLS visibility contract is not verified')
        passphrase=contract['UPGRADE_BACKUP_PASSPHRASE']
        if len(passphrase)<32 or '\n' in passphrase:raise RuntimeError('Invalid encryption contract')
        ssh=json.loads(run(['vault','kv','get','-format=json','kv/CICD/uat']))['data']['data']
        key=work/'key';key.write_bytes(base64.b64decode(ssh['SSH_PRIVATE_DEPLOY_KEY_B64'],validate=True));key.chmod(0o600)
        identity=uuid.uuid4().hex
        command=(['sudo','-n'] if user!='root' else [])+['bash','-c',REMOTE,'snapshot',identity]
        argv=['ssh','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=accept-new',user+'@'+host['ip'],shlex.join(command)]
        archive,counts,size=stream(dsn,argv,passphrase,tables)
        receipt=archive|{'schema':'prod-full-business-snapshot/v1','environment':'uat','source':'prod-supabase',
                         'source_role':'readonly_release','caller_run_id':caller,'snapshot_id':identity,
                         'table_count':len(tables),'counts':counts,'stream_bytes':size,
                         'consistent_readonly_snapshot':True,'business_database_writes':False,
                         'two_hop_import_verified':False,'success':True}
        print(json.dumps(receipt,sort_keys=True))


if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.TimeoutExpired):
        raise SystemExit('Full business source snapshot failed; private runtime output withheld')
