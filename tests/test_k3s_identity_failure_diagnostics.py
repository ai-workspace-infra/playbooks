import unittest
from pathlib import Path
import yaml
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]

class IdentityFailureDiagnosticsTest(unittest.TestCase):
    def test_nonzero_does_not_parse_empty_stdout(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/k3s_identity/tasks/main.yml').read_text())
        generation = next(t for t in tasks if t.get('name') == 'Generate verify and archive independent client credentials on master')
        report = next(t for t in tasks if t.get('name') == 'Require successful identity issuance and archival')
        self.assertTrue(generation['no_log'])
        self.assertFalse(generation['failed_when'])
        self.assertEqual(report['ansible.builtin.assert']['that'], ['k3s_identity_generation.rc == 0'])
        env = NativeEnvironment()
        env.filters['from_json'] = __import__('json').loads
        expression = env.compile_expression(generation['changed_when'])
        self.assertFalse(expression(k3s_identity_generation={'rc': 1, 'stdout': ''}))
        self.assertFalse(expression(k3s_identity_generation={'rc': 0, 'stdout': '{"created": []}'}))
        self.assertTrue(expression(k3s_identity_generation={'rc': 0, 'stdout': '{"created": ["root"]}'}))

if __name__ == '__main__':
    unittest.main()
