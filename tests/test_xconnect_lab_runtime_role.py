import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/xconnect_lab_runtime'


class XConnectLabRuntimeRoleContractTests(unittest.TestCase):
    def test_role_is_host_service_only_and_uat_only(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn("xconnect_lab_runtime_environment == 'uat'", source)
        self.assertIn("'gateway_identity', 'gateway', 'gateway_reconcile', 'one', 'gateway_verify', 'one_verify'", source)
        self.assertNotIn('terraform', source)
        self.assertNotIn('cloudflare', source)
        self.assertNotIn('aws ', source)
        self.assertNotIn('curl ', source)

    def test_runtime_operations_delegate_to_existing_roles(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn('name: vhosts/xconnect_gateway', source)
        self.assertIn('name: vhosts/xconnect_one', source)
        self.assertIn('tasks_from: identity', source)
        self.assertIn("xconnect_lab_runtime_operation == 'gateway_reconcile'", source)

    def test_verification_uses_fixed_status_commands_and_no_credentials(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn('- xconnect-gateway', source)
        self.assertIn('- xconnect', source)
        self.assertIn('no_log: true', source)
        self.assertNotIn('xconnect://', source)
        self.assertNotIn('invite', source.lower())

    def test_playbook_requires_explicit_target(self):
        source = (ROOT / 'xconnect-lab-runtime.yml').read_text()
        self.assertIn("hosts: '{{ xconnect_lab_runtime_hosts | default(\"never-matches\") }}'", source)
        self.assertIn('never runs Terraform', source)


if __name__ == '__main__':
    unittest.main()
