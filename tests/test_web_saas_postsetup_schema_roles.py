"""Regression tests for the host-side Accounts schema initialization sentinel."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
POSTSETUP = ROOT / "roles/vhosts/accounts_service/templates/postsetup.sh.j2"


class AccountsPostsetupSchemaSentinelTests(unittest.TestCase):
    def test_partial_baseline_is_not_treated_as_initialized(self) -> None:
        source = POSTSETUP.read_text()

        self.assertIn("table_name IN ('users','subscriptions')", source)
        self.assertIn('[[ "${required_tables}" != 2 ]]', source)
        self.assertIn('[[ "${verified_tables}" != 2 ]]', source)
        self.assertIn("-v ON_ERROR_STOP=1", source)

    def test_legacy_users_only_sentinel_is_gone(self) -> None:
        source = POSTSETUP.read_text()
        self.assertNotIn("table_name='users'\" | grep -q 1", source)


if __name__ == "__main__":
    unittest.main()
