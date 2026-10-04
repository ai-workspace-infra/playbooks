import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE_DIR = ROOT / "roles/vhosts/vault_port_guard"


class VaultPortGuardContractTest(unittest.TestCase):
    def test_task_file_parses(self):
        tasks = yaml.safe_load((ROLE_DIR / "tasks/main.yml").read_text(encoding="utf-8"))
        self.assertTrue(tasks)

    def test_defaults_scope_only_8200_and_8201_and_no_service_coupling(self):
        defaults = (ROLE_DIR / "defaults/main.yml").read_text(encoding="utf-8")
        self.assertIn("[8200, 8201]", defaults)
        self.assertIn("vault_port_guard_service_name: vault", defaults)
        service_unit = (ROLE_DIR / "templates/vault-port-guard.service.j2").read_text(encoding="utf-8")
        self.assertNotIn("vault_legacy_migration", service_unit)

    def test_ruleset_allows_only_loopback_and_the_declared_interface(self):
        ruleset = (ROLE_DIR / "templates/vault-port-guard.nft.j2").read_text(encoding="utf-8")
        self.assertIn('iifname "lo" accept', ruleset)
        self.assertIn("vault_port_guard_interface", ruleset)
        self.assertIn("drop", ruleset)
        # The final rule (drop) must come after both accept rules.
        self.assertLess(ruleset.index("accept"), ruleset.rindex("drop"))

    def test_disabling_removes_the_live_table_and_stops_the_unit(self):
        tasks = (ROLE_DIR / "tasks/main.yml").read_text(encoding="utf-8")
        self.assertIn("not (vault_port_guard_enabled | bool)", tasks)
        self.assertIn("nft delete table inet", tasks)
        self.assertIn("state: stopped", tasks)

    def test_enabling_verifies_the_table_actually_loaded(self):
        tasks = (ROLE_DIR / "tasks/main.yml").read_text(encoding="utf-8")
        self.assertIn("nft list table inet", tasks)


if __name__ == "__main__":
    unittest.main()
