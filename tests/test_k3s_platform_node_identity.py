from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class K3sPlatformNodeIdentityTests(unittest.TestCase):
    def test_config_renders_explicit_node_identity_and_restricted_kubeconfig_mode(self):
        source = (ROOT / 'roles/vhosts/k3s_platform_bootstrap/templates/k3s-config.yaml.j2').read_text()
        defaults = (ROOT / 'roles/vhosts/k3s_platform_bootstrap/defaults/main.yml').read_text()
        self.assertIn('node-name: "{{ k3s_platform_node_name }}"', source)
        self.assertIn('k3s_platform_node_name: "{{ inventory_hostname }}"', defaults)
        self.assertIn('write-kubeconfig-mode: "{{ k3s_platform_write_kubeconfig_mode }}"', source)
        install_tasks = (ROOT / 'roles/vhosts/k3s_platform_bootstrap/tasks/main.yml').read_text()
        self.assertIn('INSTALL_K3S_VERSION: "{{ k3s_platform_k3s_version }}"', install_tasks)
        self.assertIn('DESIRED_VERSION="{{ k3s_platform_helm_version }}"', install_tasks)
        self.assertIn('FLUX_VERSION="{{ k3s_platform_flux_version }}"', install_tasks)
        self.assertIn('preserve_hostname: true', install_tasks)
        self.assertIn('k3s_platform_retired_hostnames', install_tasks)
        self.assertIn("regexp: '^127\\.0\\.1\\.1\\s+'", install_tasks)
        self.assertIn('Remove temporary K3s and Flux bootstrap files', install_tasks)
        self.assertIn('state: absent', install_tasks)


if __name__ == '__main__':
    unittest.main()
