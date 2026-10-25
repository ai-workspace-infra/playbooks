#!/usr/bin/env python3
"""Owner-side adapter: exact CMDB host, memory-only SSH agent key, same Playbook."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]


def execute(plan, cmdb, output):
    if plan.get('schema_version')!='v5-owner-plan/v1':raise ValueError()
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if not re.fullmatch('[a-f0-9]{40}',plan['playbooks_sha']) or sha!=plan['playbooks_sha']:raise ValueError()
    subprocess.run(['git','diff','--quiet','HEAD','--','roles/v5_readonly_acceptance','.github/scripts/run-v5-acceptance.py'],cwd=ROOT,check=True)
    spec=importlib.util.spec_from_file_location('cmdb',ROOT/'inventory/terraform_cmdb.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    hosts=module.build_inventory(cmdb)['_meta']['hostvars']
    target=plan['target'];host=hosts[target['host']]
    if str(host['cmdb_instance_id'])!=target['host_id'] or host['cmdb_environment']!=plan['environment']:raise ValueError()
    if not re.fullmatch('[a-f0-9]{32}',target['machine_id']):raise ValueError()
    if not target['known_hosts'].strip() or '\n@' in target['known_hosts']:raise ValueError()
    if plan['environment'] not in {'sit','uat','prod'}:raise ValueError()
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any(output.iterdir()):raise ValueError()
    # Only explicitly selected nonsecret fields reach the Playbook.
    values={
        'v5_acceptance_target':target['host'],'v5_acceptance_environment':plan['environment'],
        'v5_acceptance_namespace':target['namespace'],'v5_acceptance_database_pod':target['database_pod'],
        'v5_acceptance_database_user':target['database_user'],'v5_acceptance_database_name':target['database_name'],
        'v5_acceptance_machine_id':target['machine_id'],'v5_acceptance_schema':target['schema'],
        'v5_acceptance_expected_agents':target['expected_agents'],'v5_acceptance_gitops_sha':plan['gitops_sha'],
        'v5_acceptance_source_name':target['source_name'],'v5_acceptance_source_namespace':target['source_namespace'],
        'v5_acceptance_output':str(output/'observation.json'),'v5_acceptance_report_dir':str(output/'report'),
        'v5_acceptance_run_id':plan['run_id'],'v5_acceptance_owner_sha':sha,'v5_acceptance_host_id':target['host_id']}
    agent={}
    with tempfile.TemporaryDirectory(prefix='v5-owner-') as temp:
        temp=Path(temp);os.chmod(temp,0o700)
        (temp/'known_hosts').write_text(target['known_hosts']+'\n')
        selected={k:host[k] for k in ('ansible_host','ansible_user','ansible_port','cmdb_environment')}
        selected['ansible_python_interpreter']='/usr/bin/python3'
        selected['ansible_ssh_common_args']='-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+str(temp/'known_hosts')
        (temp/'inventory.json').write_text(json.dumps({'all':{'hosts':{target['host']:selected}}}))
        (temp/'vars.json').write_text(json.dumps(values))
        (temp/'ansible.cfg').write_text('[defaults]\nhost_key_checking = True\nlocal_tmp = '+str(temp/'local')+'\n[ssh_connection]\nssh_args = -o BatchMode=yes -o StrictHostKeyChecking=yes\n')
        env=dict(os.environ,ANSIBLE_CONFIG=str(temp/'ansible.cfg'),ANSIBLE_NOCOLOR='1')
        try:
            raw=subprocess.check_output(['ssh-agent','-s'],text=True)
            for name in ('SSH_AUTH_SOCK','SSH_AGENT_PID'):
                match=re.search(name+r'=([^;\n]+);',raw)
                if not match:raise ValueError()
                agent[name]=match.group(1)
            env.update(agent)
            key=base64.b64decode(os.environ['V5_SSH_KEY_B64'],validate=True)
            subprocess.run(['ssh-add','-'],input=key,env=env,capture_output=True,check=True)
            result=subprocess.run(['ansible-playbook','-i',str(temp/'inventory.json'),str(ROOT/'v5-readonly-acceptance.yml'),'-e','@'+str(temp/'vars.json')],env=env,capture_output=True,text=True,timeout=300)
            (output/'playbook.log').write_text(result.stdout+result.stderr)
            return result.returncode
        finally:
            if agent:subprocess.run(['ssh-agent','-k'],env=dict(os.environ,**agent),capture_output=True)


def main():
    try:
        code=execute(json.loads(Path(os.environ['V5_OWNER_PLAN']).read_text()),json.loads(Path(os.environ['V5_CMDB_FILE']).read_text()),os.environ['V5_EVIDENCE_DIR'])
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        raise SystemExit('Owner acceptance adapter rejected scope or failed; no acceptance granted.')
    raise SystemExit(code)


if __name__=='__main__':main()
