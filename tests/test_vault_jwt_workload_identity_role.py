import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'roles/vhosts/vault_jwt_workload_identity/files'
sys.path.insert(0,str(DIR))
spec=importlib.util.spec_from_file_location('generic_identity',DIR/'execute.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def config():
    return {'mode':'verify','runtime':'kubernetes','host':'192.0.2.1','ssh_user':'operator',
        'known_hosts':'/verified/known_hosts','cluster_uid':'cluster','node_uid':'node','node_name':'example-node',
        'expected_version':'v1.33.4','vault_address':'https://vault.example.com','admin_policy':'identity-admin',
        'identity':{'mount':'jwt-team','role':'reader','policy':'reader-policy','namespace':'team-a',
          'service_account':'reader-sa','audience':'team-a-vault','denied_audience':'denied-probe',
          'probe_service_account':'probe','allowed_path':'kv/data/team-a/cert','denied_path':'kv/data/team-a/other'}}


class GenericRole(unittest.TestCase):
    def test_independent_configuration_flows_without_v6_values(self):
        c=config();calls=[]
        def remote(host,known,spec,runtime,user,env):
            calls.append((host,runtime,user,spec.namespace,'VAULT_TOKEN' in env));return object()
        def collect(cluster,node,version,name,spec,run):
            calls.append((cluster,node,version,name,spec.mount))
            return {'issuer':'issuer','public_keys':['key'],'good_jwt':'good','wrong_audience_jwt':'bad-aud','wrong_service_account_jwt':'bad-sa'}
        with patch.object(m.vault_contract,'verify',return_value={'result':'VAULT_WORKLOAD_IDENTITY_ALLOW_DENY_VERIFIED'}):
            result=m.execute(c,env={'SSH_AUTH_SOCK':'socket','VAULT_TOKEN':'not-for-ssh'},collector=collect,remote_type=remote)
        self.assertEqual(result['node_name'],'example-node')
        self.assertEqual(calls[0],('192.0.2.1','kubernetes','operator','team-a',False))
        self.assertEqual(calls[1],('cluster','node','v1.33.4','example-node','jwt-team'))

    def test_role_tasks_delegate_and_suppress_secrets(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/vault_jwt_workload_identity/tasks/main.yml').read_text())
        run=next(t for t in tasks if 'ansible.builtin.command' in t)
        self.assertEqual(run['delegate_to'],'localhost')
        self.assertTrue(run['no_log'])
        self.assertIn('stdin',run['ansible.builtin.command'])

if __name__=='__main__':unittest.main()
