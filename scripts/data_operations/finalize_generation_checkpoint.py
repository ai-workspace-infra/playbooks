#!/usr/bin/env python3
"""Complete an existing DB checkpoint using the staged Playbook role over SSH."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--generation', required=True)
    p.add_argument('--root', required=True)
    p.add_argument('--vault-dir', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--expected-schema', required=True, type=int)
    p.add_argument('--remote-playbook', required=True)
    a = p.parse_args()
    assert re.fullmatch(r'prod-v[1-9][0-9]*', a.generation)
    assert re.fullmatch(r'[1-9][0-9]*', a.run_id)
    assert re.fullmatch(r'/home/[a-zA-Z0-9_]+/[a-zA-Z0-9_/-]+/web-saas-full-snapshot.yml', a.remote_playbook)
    env = dict(os.environ, VAULT_ADDR='https://vault.svc.plus')
    def run(command):
        r = subprocess.run(command, capture_output=True, env=env)
        if r.returncode: raise RuntimeError('checkpoint prerequisite unavailable')
        return r.stdout
    key = run(['vault','kv','get','-field=web_saas_release_passphrase',f'kv/{a.generation}/backups']).decode().strip()
    assert len(key) >= 32
    metadata = {}
    for path in ['kv/prod/accounts/runtime','kv/prod/accounts/oauth/github','kv/prod/accounts/oauth/google',
                 'kv/prod/billing-service','kv/CICD/domains/svc.plus',f'kv/{a.generation}/backups']:
        response = subprocess.run(['vault','kv','get','-format=json',path],capture_output=True,env=env)
        metadata[path] = ({'keys':sorted(json.loads(response.stdout)['data']['data'])}
                          if response.returncode == 0 else {'status':'unavailable'})
    base = Path(a.vault_dir)
    payload = {'generation':a.generation,'root':a.root,'key':key,
               'vault':base64.b64encode((base/'vault-raft.snapshot.enc').read_bytes()).decode(),
               'inspect':(base/'vault-raft.inspect.txt').read_text(),'keys':metadata}
    wrapper = "import os,sys,tempfile,subprocess; os.umask(0o077); f=tempfile.NamedTemporaryFile(mode='wb'); f.write(sys.stdin.buffer.read()); f.flush(); p=subprocess.run(['ansible-playbook','-i','localhost,','-c','local',"+repr(a.remote_playbook)+",'-e','@'+f.name]); f.close(); sys.exit(p.returncode)"
    variables = {'snapshot_generation':a.generation,'snapshot_expected_schema':a.expected_schema,
                 'snapshot_run_id':a.run_id,'snapshot_passphrase':key,'snapshot_phase':'finalize',
                 'snapshot_finalize_payload':payload}
    result = subprocess.run(['gcloud','compute','ssh','web-saas-'+a.generation,
                             '--project=open-platform-prod','--zone=asia-east1-a','--quiet',
                             '--ssh-flag=-oConnectTimeout=15','--command=sudo python3 -c '+shlex.quote(wrapper)],
                            input=json.dumps(variables).encode(),capture_output=True,env=env)
    print((result.stdout+result.stderr).decode().replace(key,'[REDACTED]'))
    if result.returncode: raise RuntimeError('checkpoint finalization failed')


if __name__ == '__main__': main()
