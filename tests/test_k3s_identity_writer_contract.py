from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class WriterContract(unittest.TestCase):
    def test_dedicated_writer_is_required_before_common_and_used_for_generation(self):
        play = yaml.safe_load((ROOT / 'k3s_platform_bootstrap.yml').read_text())[0]
        tasks = play['pre_tasks']
        contract = next(i for i, t in enumerate(tasks) if t['name'] == 'Require identity configuration before node changes')
        common = next(i for i, t in enumerate(tasks) if t.get('ansible.builtin.include_role', {}).get('name') == 'vhosts/common')
        self.assertLess(contract, common)
        checks = tasks[contract]['ansible.builtin.assert']['that']
        self.assertIn("lookup('env', 'K3S_IDENTITY_VAULT_TOKEN') | length > 0", checks)
        self.assertIn("lookup('env', 'K3S_IDENTITY_VAULT_TOKEN') != lookup('env', 'VAULT_TOKEN')", checks)
        identity = yaml.safe_load((ROOT / 'roles/vhosts/k3s_identity/tasks/main.yml').read_text())
        generation = next(t for t in identity if t['name'] == 'Generate verify and archive independent client credentials on master')
        self.assertEqual("{{ lookup('env', 'K3S_IDENTITY_VAULT_TOKEN') }}", generation['environment']['VAULT_TOKEN'])
        self.assertTrue(generation['no_log'])


if __name__ == '__main__':
    unittest.main()
