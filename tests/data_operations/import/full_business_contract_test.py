import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("contract", ROOT / "scripts/data_operations/full_business_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class ScopeTest(unittest.TestCase):
    def test_scope_covers_entitlements_and_ledgers(self):
        for table in ("users", "identities", "sessions", "subscriptions", "account_quota_states", "billing_ledger", "traffic_minute_buckets"):
            self.assertIn(table, contract.BUSINESS_TABLES)
        self.assertEqual(len(contract.BUSINESS_TABLES), len(set(contract.BUSINESS_TABLES)))
        self.assertFalse(set(contract.BUSINESS_TABLES) & set(contract.CONTROL_TABLES))

    def test_source_scope_is_complete_and_new_tables_fail_closed(self):
        contract.validate_source_tables(contract.BUSINESS_TABLES + contract.CONTROL_TABLES)
        contract.validate_source_tables(contract.LEGACY_BUSINESS_TABLES)
        for tables in (contract.LEGACY_BUSINESS_TABLES[:-1], contract.BUSINESS_TABLES + ("unexpected",)):
            with self.assertRaises(ValueError):
                contract.validate_source_tables(tables)

    def test_readonly_policy_cannot_accept_arbitrary_sql_identifiers(self):
        for tables in (("users;DROP TABLE users",), ("unknown",), ()):
            with self.assertRaises(ValueError):
                contract.readonly_policy_sql(tables)
        with self.assertRaises(ValueError):
            contract.readonly_policy_sql(role="postgres")
        sql = contract.readonly_policy_sql(("users",))
        self.assertIn('GRANT SELECT ON TABLE public."users" TO readonly_release;', sql)
        self.assertIn("FOR SELECT TO readonly_release USING (true)", sql)
        self.assertNotIn("BYPASSRLS", sql)


if __name__ == "__main__":
    unittest.main()
