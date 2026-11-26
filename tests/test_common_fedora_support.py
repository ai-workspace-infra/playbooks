import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles" / "vhosts" / "common"


class CommonFedoraSupportTests(unittest.TestCase):
    def test_fedora_uses_redhat_family_dnf_path(self):
        main = (ROLE / "tasks" / "main.yml").read_text()
        redhat_tasks = (ROLE / "tasks" / "common_redhat.yml").read_text()
        packages = (ROLE / "tasks" / "packages.yml").read_text()

        self.assertIn('ansible_facts.os_family == "RedHat"', main)
        self.assertIn("package_manager: dnf", redhat_tasks)
        self.assertIn("ansible.builtin.dnf:", packages)

    def test_fedora_defaults_do_not_request_the_unavailable_uidmap_rpm(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())

        self.assertIn("uidmap", defaults["packages"]["apt"]["list"])
        self.assertNotIn("uidmap", defaults["packages"]["yum"]["list"])
        self.assertIn("fuse-overlayfs", defaults["packages"]["yum"]["list"])

    def test_firewall_can_be_disabled_for_hosts_without_firewalld(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
        main = (ROLE / "tasks" / "main.yml").read_text()
        firewall = (ROLE / "tasks" / "firewall_ports.yml").read_text()

        self.assertTrue(defaults["common_firewall"]["enabled"])
        self.assertIn("common_firewall.enabled | default(true) | bool", main)
        self.assertIn("firewalld={{ common_firewall_firewalld_binary.stat.exists", firewall)
        self.assertIn("common_firewall.enabled | default(true) | bool", firewall)

    def test_fedora_44_support_is_documented(self):
        readme = (ROLE / "README.md").read_text()
        self.assertIn("Fedora 44", readme)
        self.assertIn("DNF5", readme)
        self.assertIn("common_firewall.enabled: false", readme)


if __name__ == "__main__":
    unittest.main()
