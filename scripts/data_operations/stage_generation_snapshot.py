#!/usr/bin/env python3
"""Fetch one encrypted generation checkpoint over SSH; decrypt in a private staging directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--generation',required=True)
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--encrypted-sha256',required=True)
    args=parser.parse_args()
    assert re.fullmatch(r'prod-v[1-9][0-9]*',args.generation)
    assert re.fullmatch(r'full-snapshot-[0-9]{8}/[0-9]{8}T[0-9]{6}Z',args.snapshot)
    assert re.fullmatch(r'[0-9a-f]{64}',args.encrypted_sha256)
    os.umask(0o077)
    root=Path(tempfile.mkdtemp(prefix='generation-restore-staging-'))
    key=subprocess.run(['vault','kv','get','-field=web_saas_release_passphrase',f'kv/{args.generation}/backups'],
                       env=dict(os.environ,VAULT_ADDR='https://vault.svc.plus'),capture_output=True,check=True).stdout.decode().strip()
    assert len(key)>=32
    remote=f'/data/backups/web-saas/{args.generation}/{args.snapshot}/db/account.dump.enc'
    ciphertext=root/'account.dump.enc'
    with ciphertext.open('xb') as out:
        result=subprocess.run(['gcloud','compute','ssh','web-saas-'+args.generation,
                               '--project=open-platform-prod','--zone=asia-east1-a','--quiet',
                               '--ssh-flag=-oStrictHostKeyChecking=yes','--command=sudo cat '+remote],
                              stdout=out,stderr=subprocess.PIPE)
    if result.returncode: raise RuntimeError('encrypted checkpoint transport failed')
    h=hashlib.sha256()
    with ciphertext.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''): h.update(block)
    if h.hexdigest()!=args.encrypted_sha256: raise RuntimeError('reviewed encrypted checkpoint checksum differs')
    archive=root/'account.dump'
    with archive.open('xb') as out:
        result=subprocess.run(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000',
                               '-pass','env:SNAPSHOT_KEY','-in',str(ciphertext)],
                              env=dict(os.environ,SNAPSHOT_KEY=key),stdout=out,stderr=subprocess.PIPE)
    if result.returncode: raise RuntimeError('checkpoint decryption failed')
    def sha(path):
        h=hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''): h.update(block)
        return h.hexdigest()
    receipt={'generation':args.generation,'snapshot':args.snapshot,'archive':str(archive),
             'archive_sha256':sha(archive),'encrypted_sha256':sha(ciphertext),'bytes':archive.stat().st_size,
             'source_unchanged':True}
    (root/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__': main()
