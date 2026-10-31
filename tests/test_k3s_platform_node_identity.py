from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class K3sPlatformNodeIdentityTests(unittest.TestCase):
    def test_config_renders_explicit_node_identity_and_restricted_kubeconfig_mode(self):
        source = (ROOT / 'roles/vhosts/k3s-cluster-server/templates/k3s-config.yaml.j2').read_text()
        defaults = (ROOT / 'roles/vhosts/k3s-cluster-server/defaults/main.yml').read_text()
        self.assertIn('node-name: "{{ k3s_platform_node_name }}"', source)
        self.assertIn('k3s_platform_node_name: "{{ inventory_hostname }}"', defaults)
        self.assertIn('write-kubeconfig-mode: "{{ k3s_platform_write_kubeconfig_mode }}"', source)
        install_tasks = (ROOT / 'roles/vhosts/k3s-cluster-server/tasks/bootstrap.yml').read_text()
        self.assertIn('INSTALL_K3S_VERSION: "{{ k3s_platform_k3s_version }}"', install_tasks)
        preflight = (ROOT / 'roles/vhosts/k3s-cluster-server/tasks/preflight.yml').read_text()
        self.assertIn('k3s_control_plane_hostname.stdout | trim == k3s_platform_node_name', preflight)
        self.assertNotIn('ansible.builtin.hostname', install_tasks)
        self.assertNotIn('/etc/hosts', install_tasks)
        self.assertIn('k3s_platform_write_kubeconfig_mode: "0600"', defaults)
        self.assertIn('state: absent', install_tasks)


if __name__ == '__main__':
    unittest.main()
