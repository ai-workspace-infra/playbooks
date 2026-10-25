import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('owner_adapter',ROOT/'.github/scripts/run-v5-acceptance.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)


def inputs():
    target=dict(host='v5',host_id='123',machine_id='a'*32,known_hosts='example ssh-ed25519 public-key',namespace='test',database_pod='pg-0',database_user='app',database_name='account',schema='1',expected_agents=5,source_name='source',source_namespace='flux')
    plan=dict(schema_version='v5-owner-plan/v1',playbooks_sha='a'*40,environment='uat',gitops_sha='b'*40,target=target,run_id='test-1')
    cmdb=dict(environment='uat',hosts={'v5':dict(ip='192.0.2.1',instance_id=123,ansible_user='root')})
    return plan,cmdb


@pytest.mark.parametrize('change',['sha','environment','instance'])
def test_owner_rejects_wrong_scope_before_starting_agent(tmp_path,change):
    plan,cmdb=inputs()
    if change=='sha':plan['playbooks_sha']='b'*40
    if change=='environment':cmdb['environment']='prod'
    if change=='instance':cmdb['hosts']['v5']['instance_id']=456
    with patch.object(subprocess,'check_output',return_value='a'*40+'\n') as output,patch.object(subprocess,'run') as run:
        with pytest.raises(ValueError):adapter.execute(plan,cmdb,tmp_path/'evidence')
    assert all(call.args[0][0]!='ssh-agent' for call in output.call_args_list)


def test_key_is_only_sent_to_agent_stdin_and_agent_is_cleaned(tmp_path,monkeypatch):
    plan,cmdb=inputs();monkeypatch.setenv('V5_SSH_KEY_B64',base64.b64encode(b'synthetic-private-key').decode())
    commands=[]
    def run(args,**kwargs):
        commands.append((args,kwargs))
        if args[0]=='ansible-playbook':return subprocess.CompletedProcess(args,2,'bounded output','')
        return subprocess.CompletedProcess(args,0,'','')
    with patch.object(subprocess,'check_output',side_effect=['a'*40+'\n','SSH_AUTH_SOCK=/tmp/synthetic-agent;\nSSH_AGENT_PID=123;\n']),patch.object(subprocess,'run',side_effect=run):
        assert adapter.execute(plan,cmdb,tmp_path/'evidence')==2
    assert any(args==['ssh-add','-'] and kwargs['input']==b'synthetic-private-key' for args,kwargs in commands)
    assert any(args==['ssh-agent','-k'] for args,kwargs in commands)
    assert 'synthetic-private-key' not in ''.join(p.read_text() for p in (tmp_path/'evidence').iterdir())
