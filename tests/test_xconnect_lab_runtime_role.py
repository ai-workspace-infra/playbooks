import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/xconnect_lab_runtime'


class XConnectLabRuntimeRoleContractTests(unittest.TestCase):
    def test_role_is_host_service_only_and_uat_only(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        self.assertIn("xconnect_lab_runtime_environment == 'uat'", source)
        self.assertIn("'gateway_identity', 'gateway', 'gateway_reconcile', 'one', 'gateway_verify', 'one_verify', 'xhttp_verify', 'private_probe_setup', 'private_probe_cleanup'", source)
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

    def test_xhttp_verification_is_read_only_and_exact(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        verifier = (ROLE / 'files/verify_xhttp_runtime.py').read_text()
        self.assertIn("xconnect_lab_runtime_operation == 'xhttp_verify'", source)
        self.assertIn('changed_when: false', source)
        self.assertIn('xhttp_config_path is match', source)
        for exact_value in ('127.0.0.1', '51830', '443', '127.0.0.1:51820',
                            '/run/xconnect-gateway/xray.sock,0660'):
            self.assertIn(exact_value, verifier)
        for forbidden in ('systemctl', 'subprocess', 'terraform', 'cloudflare'):
            self.assertNotIn(forbidden, verifier.lower())

    def test_private_probe_is_same_run_bounded_and_has_cleanup(self):
        source = (ROLE / 'tasks/main.yml').read_text()
        helper = (ROLE / 'files/manage_private_probe.sh').read_text()
        self.assertIn("xconnect_lab_runtime_run_id is match('^xcl-[0-9]+-[0-9]+$')", source)
        self.assertIn("private_probe_cleanup", source)
        self.assertIn('RuntimeMaxSec=3600', helper)
        self.assertIn('trap cleanup_failed_setup EXIT', helper)
        self.assertIn('systemctl is-active --quiet', helper)
        self.assertIn('[[ "$marker" == "$run_id" ]]', helper)
        self.assertIn('systemctl stop "$unit.service"', helper)
        self.assertIn('rm -rf -- "$probe_dir"', helper)
        self.assertNotIn('0.0.0.0', helper)


if __name__ == '__main__':
    unittest.main()
