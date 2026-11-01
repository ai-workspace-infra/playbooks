import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'roles/vhosts/vault_jwt_workload_identity/files'
sys.path.insert(0,str(DIR))
import vault_contract
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
    def test_vault_key_inspection_returns_fingerprints_not_public_key_material(self):
        identity=vault_contract.IdentitySpec(mount='jwt-team',role='reader',policy='reader-policy',namespace='team-a',
            service_account='reader-sa',audience='team-a-vault',denied_audience='denied-probe',
            probe_service_account='probe',allowed_path='kv/data/team-a/cert',denied_path='kv/data/team-a/other')
        key='-----BEGIN PUBLIC KEY-----\nreview-only-key\n-----END PUBLIC KEY-----\n'
        role={**identity.role_config,'token_max_ttl':600,'token_explicit_max_ttl':600}
        class Client:
            def request(self,method,path,body=None,read_json=True):
                table={
                    'sys/auth':{'data':{'jwt-team/':{'type':'jwt'}}},
                    'sys/policies/acl/reader-policy':{'data':{'policy':identity.policy_hcl}},
                    'auth/jwt-team/role/reader':{'data':role},
                    'auth/jwt-team/config':{'data':{'bound_issuer':'issuer','jwt_validation_pubkeys':[key]}},
                }
                return 200,table[path]
        result=vault_contract.inspect_signing_keys(Client(),identity,'issuer',[key])
        self.assertTrue(result['keys_match'])
        self.assertNotIn(key,str(result))
        self.assertRegex(result['current_key_sha256'][0],r'^[0-9a-f]{64}$')

    def test_independent_configuration_flows_without_v6_values(self):
        c=config();calls=[]
        def remote(host,known,spec,runtime,user,env):
            calls.append((host,runtime,user,spec.namespace,'VAULT_TOKEN' in env));return object()
        def collect(cluster,node,version,name,spec,run,include_tokens=True):
            calls.append((cluster,node,version,name,spec.mount))
            return {'issuer':'issuer','public_keys':['key'],'good_jwt':'good','wrong_audience_jwt':'bad-aud','wrong_service_account_jwt':'bad-sa'}
        with patch.object(m.vault_contract,'verify',return_value={'result':'VAULT_WORKLOAD_IDENTITY_ALLOW_DENY_VERIFIED'}):
            result=m.execute(c,env={'SSH_AUTH_SOCK':'socket','VAULT_TOKEN':'not-for-ssh'},collector=collect,remote_type=remote)
        self.assertEqual(result['node_name'],'example-node')
        self.assertEqual(calls[0],('192.0.2.1','kubernetes','operator','team-a',False))
        self.assertEqual(calls[1],('cluster','node','v1.33.4','example-node','jwt-team'))

    def test_inspect_uses_scoped_admin_but_emits_only_fingerprints(self):
        c=config();c['mode']='inspect';calls=[]
        def remote(host,known,spec,runtime,user,env):
            calls.append(('ssh-token-isolated','VAULT_TOKEN' not in env));return object()
        def collect(cluster,node,version,name,spec,run,include_tokens=True):
            calls.append(('token_requests',include_tokens))
            return {'issuer':'issuer','public_keys':['live-pem']}
        class Client:
            def __init__(self,address,token=None):self.token=token
            def request(self,method,path,body=None,read_json=True):
                if path=='auth/token/lookup-self':
                    return 200,{'data':{'policies':['identity-admin'],'ttl':600}}
                return 500,None
        with patch.object(m.vault_contract,'inspect_signing_keys',return_value={
                'result':'VAULT_WORKLOAD_IDENTITY_TRUST_INSPECTED','current_key_sha256':['a'*64],
                'target_key_sha256':['b'*64],'keys_match':False,'issuer_matches':True}):
            result=m.execute(c,env={'SSH_AUTH_SOCK':'socket','VAULT_TOKEN':'scoped'},collector=collect,
                             remote_type=remote,client_type=Client)
        self.assertEqual(calls,[('ssh-token-isolated',True),('token_requests',False)])
        self.assertNotIn('live-pem',str(result))
        self.assertEqual(result['result'],'VAULT_WORKLOAD_IDENTITY_TRUST_INSPECTED')

    def test_flux_release_is_not_an_identity_operation(self):
        c = config()
        c['mode'] = 'release-root'
        with self.assertRaises(ValueError):
            m.execute(c, env={'SSH_AUTH_SOCK': 'socket'})
        self.assertFalse(hasattr(m.remote_kubectl.RemoteKubectl, 'apply_manifest'))

    def test_role_tasks_delegate_and_suppress_secrets(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/vault_jwt_workload_identity/tasks/main.yml').read_text())
        run=next(t for t in tasks if 'ansible.builtin.command' in t)
        self.assertEqual(run['delegate_to'],'localhost')
        self.assertTrue(run['no_log'])
        self.assertIn('stdin',run['ansible.builtin.command'])

if __name__=='__main__':unittest.main()
