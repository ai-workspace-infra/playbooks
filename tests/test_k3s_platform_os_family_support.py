import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles' / 'vhosts' / 'k3s-cluster-server'


class K3sPlatformOsFamilySupportTest(unittest.TestCase):
    '''The K3s platform bootstrap must support Debian 13 and Rocky Linux 10.x.'''

    def setUp(self):
        self.vars = yaml.safe_load((ROLE / 'vars' / 'main.yml').read_text())
        self.bootstrap = (ROLE / 'tasks' / 'bootstrap.yml').read_text()
        self.preflight = (ROLE / 'tasks' / 'preflight.yml').read_text()
        self.playbook = (ROOT / 'k3s_platform_bootstrap.yml').read_text()
        self.defaults = yaml.safe_load(
            (ROOT / 'roles' / 'vhosts' / 'common' / 'defaults' / 'main.yml').read_text()
        )

    def test_supported_families_are_declared(self):
        self.assertEqual(self.vars['k3s_platform_supported_os_families'], ['Debian', 'RedHat'])
        self.assertEqual(self.defaults['common_os_identity_supported_families'], ['Debian', 'RedHat'])

    def test_each_family_uses_its_own_openssh_client_package(self):
        packages = self.vars['k3s_platform_bootstrap_packages']
        self.assertEqual(set(packages), {'Debian', 'RedHat'})
        self.assertIn('openssh-client', packages['Debian'])
        self.assertNotIn('openssh-clients', packages['Debian'])
        self.assertIn('openssh-clients', packages['RedHat'])
        self.assertNotIn('openssh-client', packages['RedHat'])

    def test_both_families_install_the_same_baseline_tools(self):
        packages = self.vars['k3s_platform_bootstrap_packages']
        required = {'curl', 'git', 'ca-certificates', 'python3-pip', 'tar'}
        for family in ('Debian', 'RedHat'):
            self.assertTrue(required.issubset(set(packages[family])), family)

    def test_bootstrap_asserts_family_and_reads_the_map(self):
        self.assertIn('ansible_os_family in k3s_platform_supported_os_families', self.bootstrap)
        self.assertIn('k3s_platform_bootstrap_packages[ansible_os_family]', self.bootstrap)
        install_block = self.bootstrap.split('- name: Install bootstrap dependencies')[1]
        install_block = install_block.split('state: present')[0]
        self.assertNotIn('openssh-client', install_block)

    def test_platform_bootstrap_honours_the_declared_data_mode(self):
        self.assertIn(
            "k3s_platform_data_mode | default('volume') in ['volume', 'directory']",
            self.playbook,
        )
        volume_assert = self.playbook.split(
            '- name: Require the declared independent data volume when the node uses volume mode'
        )[1].split('- name: Consume declared platform')[0]
        self.assertIn("when: bootstrap_node_data_mode == 'volume'", volume_assert)
        identity_assert = self.playbook.split(
            '- name: Require one matching node and matching cloud scope'
        )[1].split('- name: Bind the declared data mode')[0]
        self.assertNotIn('data_volume.linode_id', identity_assert)

    def test_common_requires_data_volume_only_in_volume_mode(self):
        expected = 'common_node_require_data_volume: ' + chr(39) + '{{ bootstrap_node_data_mode == "volume" }}' + chr(39)
        self.assertIn(expected, self.playbook)

    def test_os_identity_accepts_both_families(self):
        os_identity = (ROOT / 'roles' / 'vhosts' / 'common' / 'tasks' / 'os_identity.yml').read_text()
        self.assertIn('ansible_os_family in (common_os_identity_supported_families', os_identity)
        self.assertNotIn("ansible_os_family == 'Debian'", os_identity)

    def test_fail2ban_does_not_hard_fail_without_the_package(self):
        fail2ban = (ROOT / 'roles' / 'vhosts' / 'common' / 'tasks' / 'fail2ban.yml').read_text()
        self.assertIn('common_fail2ban_installable', fail2ban)
        self.assertIn('dnf, -q, list, --available, fail2ban', fail2ban)
        jail = fail2ban.split('- name: Fail2ban | Deploy jail.local configuration')[1]
        self.assertIn('common_fail2ban_installable | bool', jail)


    def test_instance_identity_pattern_is_provider_owned(self):
        self.assertIn('bootstrap_instance_id_patterns:', self.playbook)
        self.assertIn("akamai-cloud: '^[1-9][0-9]*$'", self.playbook)
        self.assertIn('bootstrap_instance_id_pattern_default', self.playbook)
        self.assertIn(
            'cmdb_instance_id | string is match(bootstrap_instance_id_patterns.get(',
            self.playbook,
        )
        self.assertNotIn("- cmdb_instance_id | string is match('^[1-9][0-9]*$')", self.playbook)

    def test_preflight_rejects_overlapping_cluster_networks(self):
        self.assertIn('Verify the declared cluster networks are distinct and internally consistent', self.preflight)
        self.assertIn('k3s_platform_cluster_dns', self.preflight)


if __name__ == '__main__':
    unittest.main()
