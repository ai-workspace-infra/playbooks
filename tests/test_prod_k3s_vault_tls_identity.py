import importlib.util
from pathlib import Path
import sys
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('entry',ROOT/'scripts/prod_k3s_vault_tls_identity.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class OwnerContract(unittest.TestCase):
    def test_target_identity_comes_from_reviewed_checkout(self):
        text=(ROOT/'scripts/prod_k3s_vault_tls_identity.py').read_text()
        self.assertIn("operation_file.is_relative_to(checkout)",text)
        self.assertIn("op['identity'].items()",text)
        self.assertIn("vault_contract.IdentitySpec(**identity)",text)
        self.assertIn("op['evidence']['bootstrap']['fileSha256']",text)

if __name__=='__main__':unittest.main()
