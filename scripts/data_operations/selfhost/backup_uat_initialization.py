#!/usr/bin/env python3
"""Encrypted pre-initialization backup; absence of a ledger is recorded honestly."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from mount_uat_retained_volume import disk_contract, run


def main():
    if os.environ.get('CONFIRM_UAT_INITIALIZATION_BACKUP') != 'true':
        raise RuntimeError('Explicit initialization backup confirmation required')
    caller=os.environ.get('CALLER_RUN_ID','')
    if not re.fullmatch(r'[1-9][0-9]*',caller): raise RuntimeError('Approved IaC caller required')
    root=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='uat-initialization-backup-') as tmp:
        work=Path(tmp)
        run(['gh','run','download',caller,'--repo','ai-workspace-infra/platform-ops-toolkit','--name','platform-ops-toolkit-cmdb','--dir',tmp])
        env=dict(os.environ,REQUESTED_ENVIRONMENT='uat',CALLER_RUN_ID=caller,
                 CONFIG_JSON=json.dumps({'target_host':'web-saas-uat'}),GH_TOKEN=run(['gh','auth','token']).strip(),CMDB_FILE=str(work/'cmdb.json'))
        run(['python3',str(root/'verify_caller_cmdb.py')],env=env)
        meta=json.loads(run(['gh','api','repos/ai-workspace-infra/platform-ops-toolkit/actions/runs/'+caller]))
        if meta.get('conclusion')!='success': raise RuntimeError('Caller must be successful')
        host=json.loads((work/'cmdb.json').read_text())['web-saas-uat'];disk_contract(host)
        user=host.get('ansible_user','root')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user): raise RuntimeError('Invalid SSH identity')
        ssh=json.loads(run(['vault','kv','get','-format=json','kv/CICD/uat']))['data']['data']
        contract=json.loads(run(['vault','kv','get','-format=json','kv/uat/database-upgrade']))['data']['data']
        if contract.get('BOOTSTRAP_STATE')!='ready': raise RuntimeError('Full business credentials are not ready')
        passphrase=contract['UPGRADE_BACKUP_PASSPHRASE']
        if len(passphrase)<32 or '\n' in passphrase: raise RuntimeError('Invalid encryption input')
        key=work/'key';key.write_bytes(base64.b64decode(ssh['SSH_PRIVATE_DEPLOY_KEY_B64'],validate=True));key.chmod(0o600)
        program=(root/'initialization_backup_host.sh').read_text()
        # Program is non-sensitive; passphrase travels only through SSH stdin.
        command=(['sudo','-n'] if user!='root' else [])+['bash','-c',program]
        output=run(['ssh','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=accept-new',user+'@'+host['ip'],shlex.join(command)],input=passphrase+'\n')
        receipt=json.loads(output.strip().splitlines()[-1])
        if receipt.get('schema')!='uat-initialization-backup/v1' or not receipt.get('isolated_restore_verified'):
            raise RuntimeError('Initialization backup receipt is not verified')
        receipt['caller_run_id']=caller
        print(json.dumps(receipt,sort_keys=True))


if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.TimeoutExpired):
        raise SystemExit('UAT initialization backup failed; private runtime output withheld')
