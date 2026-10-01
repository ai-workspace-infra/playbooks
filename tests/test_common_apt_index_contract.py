import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FAIL2BAN = ROOT / "roles" / "vhosts" / "common" / "tasks" / "fail2ban.yml"


class CommonAptIndexContractTest(unittest.TestCase):
    def test_apt_index_refresh_reports_the_failing_source(self):
        tasks = yaml.safe_load(FAIL2BAN.read_text(encoding="utf-8"))
        names = [task["name"] for task in tasks]
        refresh = tasks[names.index("Fail2ban | Refresh the apt package index (Debian/Ubuntu)")]
        install = tasks[names.index("Fail2ban | Install Fail2ban package (Debian/Ubuntu via apt)")]

        self.assertLess(names.index(refresh["name"]), names.index(install["name"]))
        self.assertEqual(refresh["ansible.builtin.command"]["argv"], ["apt-get", "update"])
        self.assertEqual(refresh["until"], "common_apt_index.rc == 0")
        self.assertIs(refresh["changed_when"], False)
        self.assertIs(refresh["become"], True)
        self.assertEqual(refresh["when"], "ansible_os_family == 'Debian'")
        # The opaque module refresh must not run again after the explicit one.
        self.assertNotIn("update_cache", install["ansible.builtin.apt"])


if __name__ == "__main__":
    unittest.main()
