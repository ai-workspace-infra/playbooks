#!/usr/bin/env python3
"""Mount only the canonical freshly attached retained UAT backup volume.

The input is an approved successful IaC caller artifact, never a hand inventory.
Refuse existing filesystem signatures, partitions, mounts, nonempty /data, and
conflicting fstab records; mkfs never runs on a previously formatted disk.
"""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

NAME = "web-saas-uat-upgrade-data"


def disk_contract(record):
    disks = record.get('persistent_data_disks', [])
    expected_id = 'projects/open-platform-uat/zones/asia-east1-a/disks/' + NAME
    if (record.get('name') != 'web-saas-uat' or record.get('provider') != 'gcp-cloud'
            or record.get('zone') != 'asia-east1-a' or len(disks) != 1
            or disks[0] != {'name': NAME, 'id': expected_id,
                            'zone': 'asia-east1-a', 'device_name': NAME,
                            'mount_path': '/data', 'management': 'google_compute_attached_disk'}):
        raise ValueError('Canonical retained volume CMDB contract is not verified')
    return disks[0]


REMOTE = r'''set -euo pipefail
umask 077
device=/dev/disk/by-id/google-web-saas-uat-upgrade-data
test -L "$device"
actual=$(readlink -f "$device")
test -b "$actual"
test "$(lsblk -dn -o TYPE "$actual" | xargs)" = disk
test "$(blockdev --getsize64 "$actual")" -ge 53687091200
test "$(lsblk -n -o NAME "$actual" | wc -l)" -eq 1
test ! -L /data
test "$(realpath -m /data)" = /data
if mountpoint -q /data; then
 test "$(readlink -f "$(findmnt -n -o SOURCE --target /data)")" = "$actual"
 test "$(findmnt -n -o FSTYPE --target /data)" = ext4
else
 test -z "$(lsblk -n -o MOUNTPOINTS "$actual" | tr -d '[:space:]')"
 test ! -e /data || test -d /data
 test ! -d /data || test -z "$(find /data -mindepth 1 -maxdepth 1 -print -quit)"
 filesystem=$(blkid -s TYPE -o value "$actual" || true)
 if test -z "$filesystem"; then
  test -z "$(wipefs --no-act --noheadings --output TYPE "$actual")"
  mkfs.ext4 -q -L uat-upgrade-data "$actual"
 else
  # Retry only an already formatted canonical volume, identified by our label.
  test "$filesystem" = ext4
  test "$(blkid -s LABEL -o value "$actual")" = uat-upgrade-data
 fi
 uuid=$(blkid -s UUID -o value "$actual")
 test -n "$uuid"
 if awk '$1 !~ /^#/ && $2=="/data" {found=1} END {exit !found}' /etc/fstab; then
  awk -v expected="UUID=$uuid" '$1 !~ /^#/ && $2=="/data" {if ($1!=expected || $3!="ext4") exit 1; found++} END {if(found!=1) exit 1}' /etc/fstab
 else
  printf 'UUID=%s /data ext4 defaults,nosuid,nodev 0 2\n' "$uuid" >> /etc/fstab
 fi
 install -d -m 700 -o root -g root /data
 mount /data
fi
test "$(readlink -f "$(findmnt -n -o SOURCE --target /data)")" = "$actual"
test "$(findmnt -n -o TARGET --target /data)" = /data
test "$(findmnt -n -o FSTYPE --target /data)" = ext4
uuid=$(blkid -s UUID -o value "$actual")
awk -v expected="UUID=$uuid" '$1 !~ /^#/ && $2=="/data" {if ($1!=expected || $3!="ext4") exit 1; found++} END {if(found!=1) exit 1}' /etc/fstab
install -d -m 700 -o root -g root /data/backups
sync
printf 'canonical-retained-uat-volume-mounted\n'
'''


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError('Retained volume owner operation failed; private output withheld')
    return result.stdout


def main():
    if os.environ.get('CONFIRM_UAT_RETAINED_VOLUME_MOUNT') != 'true':
        raise RuntimeError('Explicit canonical UAT retained volume mount confirmation required')
    caller = os.environ.get('CALLER_RUN_ID', '')
    if not re.fullmatch(r'[1-9][0-9]*', caller):
        raise RuntimeError('Successful IaC caller identity required')
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='uat-volume-mount-') as tmp:
        work = Path(tmp)
        run(['gh','run','download',caller,'--repo','ai-workspace-infra/platform-ops-toolkit','--name','platform-ops-toolkit-cmdb','--dir',tmp])
        env = dict(os.environ, REQUESTED_ENVIRONMENT='uat',CALLER_RUN_ID=caller,
                   CONFIG_JSON=json.dumps({'target_host':'web-saas-uat'}),
                   GH_TOKEN=run(['gh','auth','token']).strip(),CMDB_FILE=str(work/'cmdb.json'))
        run(['python3',str(root/'verify_caller_cmdb.py')],env=env)
        metadata=json.loads(run(['gh','api','repos/ai-workspace-infra/platform-ops-toolkit/actions/runs/'+caller]))
        if metadata.get('conclusion') != 'success':
            raise RuntimeError('IaC volume caller must have completed successfully')
        host=json.loads((work/'cmdb.json').read_text())['web-saas-uat']
        disk_contract(host)
        user=host.get('ansible_user','root')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user):
            raise RuntimeError('Target SSH identity is invalid')
        contract=json.loads(run(['vault','kv','get','-format=json','kv/CICD/uat']))['data']['data']
        key=work/'key'
        key.write_bytes(base64.b64decode(contract['SSH_PRIVATE_DEPLOY_KEY_B64'],validate=True))
        key.chmod(0o600)
        command=(['sudo','-n'] if user!='root' else [])+['bash','-s']
        output=run(['ssh','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=accept-new',user+'@'+host['ip'],shlex.join(command)],input=REMOTE)
        if 'canonical-retained-uat-volume-mounted' not in output.splitlines():
            raise RuntimeError('Mounted retained volume verification failed')
        print(json.dumps({'schema':'uat-retained-volume-mount/v1','environment':'uat',
                          'target_host':'web-saas-uat','caller_run_id':caller,'device_name':NAME,
                          'mount_path':'/data','filesystem':'ext4','fstab_verified':True,
                          'backup_restore_verified':False,'success':True},sort_keys=True))


if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.TimeoutExpired):
        raise SystemExit('UAT retained volume mount failed; private output withheld')
