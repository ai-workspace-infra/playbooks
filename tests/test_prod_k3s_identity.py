import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
from unittest import mock
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('identity_setup',ROOT/'scripts/prod_k3s_identity_setup.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class InitializerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('openssl') and shutil.which('ssh-agent'), 'OpenSSL and SSH agent required')
    def test_real_throwaway_private_key_is_validated_without_a_key_file(self):
        key = subprocess.check_output(['openssl', 'genrsa', '2048'], stderr=subprocess.DEVNULL, text=True)
        public = m.public_key(key)
        self.assertTrue(public.startswith('ssh-rsa '))
        self.assertEqual(2, len(public.split()))
        with self.assertRaises(ValueError):
            m.public_key('invalid fixture key')

    def execute(self, pubkey='ssh-ed25519 allowed', existing=None):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'scripts/vault/roles').mkdir(parents=True);(root/'scripts/vault/policies').mkdir()
            (root/'resources/svc.plus/prod/akamai').mkdir(parents=True)
            (root/'resources/svc.plus/prod/akamai/prod-k3s-v6.yaml').write_text(yaml.safe_dump({'ssh_keys':[{'public':'ssh-ed25519 allowed comment'}]}))
            for name in m.NAMES:
                role={'token_policies':[name],'token_type':'batch','token_explicit_max_ttl':1200,'bound_claims':{'repository':'ai-workspace-infra/Platform-Orchestrator','environment':'prod','ref':['refs/tags/v6.*'],'ref_protected':'true'}}
                (root/'scripts/vault/roles'/f'{name}.json').write_text(json.dumps(role))
                (root/'scripts/vault/policies'/f'{name}.hcl').write_text('path "kv/data/exact" { capabilities = ["read"] }')
            def vault(args,data=None,missing=False):
                calls.append((args,data))
                if args[:2]==['kv','get']:
                    if args[-1]==m.SSH_PATH:
                        return json.dumps({'data':{'data':{'SSH_PRIVATE_KEY':'fixture'}}}) if existing else None
                    return json.dumps({'data':{'data':{'SSH_PRIVATE_DEPLOY_KEY_B64':base64.b64encode(b'fixture').decode()}}})
                return None
            def git(args,**kwargs):return 'a'*40+'\n' if 'rev-parse' in args else ''
            argv=['setup','--orchestrator',tmp,'--orchestrator-sha','a'*40,'--gitops',tmp,'--gitops-sha','a'*40]
            with mock.patch.object(m,'vault',side_effect=vault),mock.patch.object(m,'public_key',return_value=pubkey),mock.patch.object(m.subprocess,'check_output',side_effect=git),mock.patch.dict(m.os.environ,{'VAULT_ADDR':'https://vault.svc.plus'}),mock.patch('sys.argv',argv),contextlib.redirect_stdout(io.StringIO()):
                try:m.main()
                except ValueError:return calls,False
            return calls,True
    def test_key_mismatch_prevents_all_mutation(self):
        calls,ok=self.execute(pubkey='ssh-ed25519 wrong')
        self.assertFalse(ok)
        self.assertFalse(any(args[0]=='write' or args[:2] in (['policy','write'],['kv','put']) for args,data in calls))
    def test_new_binding_uses_create_only_cas(self):
        calls,ok=self.execute();self.assertTrue(ok)
        writes=[args for args,data in calls if args[:2]==['kv','put']]
        self.assertEqual([['kv','put','-cas=0',m.SSH_PATH,'-']],writes)
        self.assertEqual(2,len([a for a,d in calls if a[0]=='write']))
    def test_existing_valid_binding_not_rewritten(self):
        calls,ok=self.execute(existing=True);self.assertTrue(ok)
        self.assertFalse(any(args[:2]==['kv','put'] for args,data in calls))

if __name__=='__main__':unittest.main()
