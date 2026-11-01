import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]


class IdentitySubresourceTest(unittest.TestCase):
    def test_secret_read_is_governed_by_declared_identity_checks(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/k3s_identity/tasks/identity_user.yml').read_text())
        declared = next(t for t in tasks if t.get('name') == 'Validate declared allow and deny cases with actual credentials')
        mandatory = next(t for t in tasks if t.get('name') == 'Require mandatory sensitive-operation denials under actual non-admin identity')
        self.assertEqual(declared['loop'], '{{ k3s_identity_checks[k3s_identity_role] }}')
        self.assertNotIn({'verb': 'get', 'resource': 'secrets'}, mandatory['loop'])
        self.assertIn({'verb': 'create', 'resource': 'pods/exec'}, mandatory['loop'])

    def test_kubectl_receives_subresource_as_option(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/k3s_identity/tasks/identity_user.yml').read_text())
        selected = [t for t in tasks if t.get('name') in (
            'Validate declared allow and deny cases with actual credentials',
            'Require mandatory sensitive-operation denials under actual non-admin identity')]
        self.assertEqual(len(selected), 2)
        for task in selected:
            template = NativeEnvironment().from_string(task['ansible.builtin.command']['argv'])
            kwargs = {'k3s_identity_role': 'operator', 'k3s_identity_checks': {'operator': [{'namespace': 'apps'}]}}
            for resource, expected in [('deployments/scale', ['deployments', '--namespace', 'apps', '--subresource=scale']), ('secrets', ['secrets', '--namespace', 'apps'])]:
                item = {'verb': 'update', 'resource': resource, 'namespace': 'apps'}
                self.assertEqual(template.render(item=item, **kwargs), ['kubectl', 'auth', 'can-i', 'update'] + expected)

if __name__ == '__main__':
    unittest.main()
