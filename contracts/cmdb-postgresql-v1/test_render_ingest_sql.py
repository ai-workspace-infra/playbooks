import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ingest", Path(__file__).with_name("render_ingest_sql.py"))
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class IngestTests(unittest.TestCase):
    def test_sql_literals_and_nul(self):
        self.assertEqual(ingest.literal("name'; DROP TABLE x;--"), "'name''; DROP TABLE x;--'")
        with self.assertRaises(ValueError):
            ingest.literal("bad\x00")

    def test_failure_receipt_is_transactional_and_does_not_delete(self):
        document = {"schema_version": "cmdb.observations.v1", "run": {"run_id": "run-a", "collector": "gcp-compute", "owner_sha": "a" * 40, "scope": "shared", "started_at": "2026-10-09T00:00:00Z", "completed_at": "2026-10-09T00:01:00Z", "outcome": "failed", "scope_complete": False, "error_class": "http_403"}, "observations": []}
        sql = ingest.render(document)
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("IS DISTINCT FROM", sql)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("cmdb.resources", sql)


if __name__ == "__main__":
    unittest.main()
