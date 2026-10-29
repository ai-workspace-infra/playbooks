from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ActionContract(unittest.TestCase):
    def test_reviewed_evidence_and_two_stage_owner_contract(self):
        action = yaml.safe_load((ROOT/'.github/actions/prod-k3s-vault-tls-identity/action.yml').read_text())
        required = {'mode','operation-path','cmdb','cmdb-sha256','gitops','gitops-sha','bootstrap-receipt',
                    'bootstrap-sha256','bootstrap-gitops-sha','known-hosts','receipt'}
        self.assertEqual(set(action['inputs']), required)
        self.assertTrue(all(item['required'] for item in action['inputs'].values()))
        step = action['runs']['steps'][-1]
        script = step['run']
        self.assertIn('env -u VAULT_TOKEN -u SSH_PRIVATE_KEY ssh-agent -s', script)
        self.assertIn('env -u VAULT_TOKEN -u SSH_PRIVATE_KEY ssh-add -', script)
        self.assertIn('unset SSH_PRIVATE_KEY', script)
        self.assertIn('prod_k3s_vault_tls_identity.py', script)
        self.assertNotIn('set -x', script)
        self.assertNotIn('kubectl', script)

if __name__ == '__main__': unittest.main()
