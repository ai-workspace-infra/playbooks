"""Regression tests for the host-side Accounts schema initialization sentinel."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
POSTSETUP = ROOT / "roles/vhosts/accounts_service/templates/postsetup.sh.j2"


class AccountsPostsetupSchemaSentinelTests(unittest.TestCase):
    def test_partial_baseline_is_not_treated_as_initialized(self) -> None:
        source = POSTSETUP.read_text()

        self.assertIn("table_name IN ('users','subscriptions')", source)
        self.assertIn('public_table_count=', source)
        self.assertIn('[[ "${public_table_count}" == 0 ]]', source)
        self.assertIn('[[ "${users_table_exists}" == 1 && "${subscriptions_table_exists}" != 1 ]]', source)
        self.assertIn('CREATE TABLE IF NOT EXISTS public.subscriptions', source)
        self.assertIn('[[ "${required_tables}" != 2 ]]', source)
        self.assertIn("-v ON_ERROR_STOP=1", source)

    def test_nonempty_database_never_runs_full_baseline(self) -> None:
        source = POSTSETUP.read_text()

        self.assertIn('Never run the full Accounts baseline against a non-empty database', source)
        self.assertIn('refusing destructive initialization', source)
        self.assertNotIn(
            'if [[ "${required_tables}" != 2 ]]; then\n'
            '  docker exec -i postgresql psql -U postgres -d account -v ON_ERROR_STOP=1 -f "${schema_file}"',
            source,
        )

    def test_legacy_users_only_sentinel_is_gone(self) -> None:
        source = POSTSETUP.read_text()
        self.assertNotIn("table_name='users'\" | grep -q 1", source)


if __name__ == "__main__":
    unittest.main()
