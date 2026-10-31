import unittest
from pathlib import Path
import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]

class DefaultKubeconfigTest(unittest.TestCase):
    def test_default_mapping_is_private_and_role_scoped(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/k3s_identity/tasks/identity_user.yml').read_text())
        by_name = {t.get('name'): t for t in tasks}
        install = by_name['Deliver master-generated private Kubernetes configuration as its OS owner']
        self.assertEqual(install['ansible.builtin.copy']['mode'], '0600')
        link = by_name['Make the user identity the default kubectl config']
        self.assertFalse(link['ansible.builtin.file']['force'])
        env = Environment()
        for role, home in [('operator', '/home/operator'), ('auditor', '/home/auditor'), ('root', '/root')]:
            variables = {'k3s_identity_role': role, 'getent_passwd': {role: ['', '', '', '', home]}}
            for key, suffix in [('src', '/.kube/identity-config'), ('dest', '/.kube/config')]:
                self.assertEqual(env.from_string(link['ansible.builtin.file'][key]).render(**variables), home + suffix)
        guard = by_name['Reject an unmanaged default kubeconfig']
        check = env.compile_expression(guard['ansible.builtin.assert']['that'][0])
        context = {'k3s_identity_role': 'auditor', 'getent_passwd': {'auditor': ['', '', '', '', '/home/auditor']}}
        self.assertTrue(check(**context, k3s_identity_default_config={'stat': {'exists': False}}))
        self.assertFalse(check(**context, k3s_identity_default_config={'stat': {'exists': True, 'islnk': False}}))
        self.assertFalse(check(**context, k3s_identity_default_config={'stat': {'exists': True, 'islnk': True, 'lnk_target': '/root/.kube/config'}}))
        self.assertTrue(check(**context, k3s_identity_default_config={'stat': {'exists': True, 'islnk': True, 'lnk_target': '/home/auditor/.kube/identity-config'}}))
        verify = by_name['Require the default Kubernetes identity to match its assigned subject']
        self.assertIn('k3s_identity_role', verify['ansible.builtin.assert']['that'][0])

if __name__ == '__main__': unittest.main()
