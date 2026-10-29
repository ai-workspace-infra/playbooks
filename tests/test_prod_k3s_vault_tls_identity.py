import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec=importlib.util.spec_from_file_location('entry', ROOT/'scripts/prod_k3s_vault_tls_identity.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ADMIN='github-actions-platform-ops-toolkit-prod-k3s-v6-vault-tls-admin'


class ClientTests(unittest.TestCase):
    def test_exact_short_lived_admin_is_accepted(self):
        class C:
            def __init__(self,*args): pass
            def request(self,*args): return 200, {'data': {'policies': [ADMIN], 'ttl': 599}}
        with patch.object(m.vault_contract,'Client',C):
            self.assertIsInstance(m.client_factory('token')(True),C)

    def test_root_extra_and_long_lived_tokens_fail(self):
        for policies, ttl in [(['root'],599),([ADMIN,'default'],599),([ADMIN],1201),([ADMIN],0)]:
            class C:
                def __init__(self,*args): pass
                def request(self,*args): return 200, {'data': {'policies': policies, 'ttl': ttl}}
            with self.subTest(policies=policies,ttl=ttl), patch.object(m.vault_contract,'Client',C):
                with self.assertRaises(ValueError): m.client_factory('token')(True)

    def test_admin_credential_never_required_for_workload_verification(self):
        class C:
            def __init__(self,address,token): self.token=token
        with patch.object(m.vault_contract,'Client',C):
            self.assertEqual(m.client_factory(None)(False,'short-lived').token,'short-lived')
            with self.assertRaises(ValueError): m.client_factory(None)(True)

if __name__=='__main__': unittest.main()
