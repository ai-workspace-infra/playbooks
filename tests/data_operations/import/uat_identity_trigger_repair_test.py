import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("repair", ROOT / "scripts/data_operations/selfhost/repair_uat_identity_triggers.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class TriggerRepairTest(unittest.TestCase):
    def test_requires_explicit_confirmation_before_any_runtime_access(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(repair, "run") as run:
            with self.assertRaises(RuntimeError):
                repair.main()
            run.assert_not_called()

    def test_requires_plain_successful_caller_identifier(self):
        with patch.dict(os.environ, {"CONFIRM_UAT_IDENTITY_TRIGGER_REPAIR": "true", "CALLER_RUN_ID": "main;unsafe"}, clear=True), patch.object(repair, "run") as run:
            with self.assertRaises(RuntimeError):
                repair.main()
            run.assert_not_called()

    def test_sql_is_additive_atomic_and_rolls_back_row_probes(self):
        sql = repair.SQL
        for expected in ["BEGIN;", "COMMIT;", "ROLLBACK TO SAVEPOINT trigger_probe;",
                         "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS version",
                         "ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS version",
                         "ALTER TABLE public.sessions ADD COLUMN IF NOT EXISTS updated_at",
                         "lock_timeout", "statement_timeout", "repair_counts"]:
            self.assertIn(expected, sql)
        for forbidden in ["DROP TABLE", "TRUNCATE", "DELETE FROM", "DISABLE TRIGGER"]:
            self.assertNotIn(forbidden, sql)


if __name__ == "__main__":
    unittest.main()
