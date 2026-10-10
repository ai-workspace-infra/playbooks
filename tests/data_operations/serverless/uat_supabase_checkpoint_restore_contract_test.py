#!/usr/bin/env python3
"""Offline contract checks for the UAT Supabase SQL checkpoint adapter."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "roles/web_saas_supabase_checkpoint_restore/files/restore_supabase_sql_gzip_verify.sh"
ROLE_TASKS = ROOT / "roles/web_saas_supabase_checkpoint_restore/tasks/main.yml"


class SupabaseCheckpointRestoreContractTests(unittest.TestCase):
    def test_adapter_declares_format_and_fail_closed_guards(self):
        source = SCRIPT.read_text()
        for value in (
            "supabase-public-sql-gzip-aes256-cbc-v1",
            "WEB_SAAS_ARCHIVE_SHA256",
            "WEB_SAAS_SOURCE_IDENTITY_STATUS",
            "unavailable_from_historical_checkpoint",
            "WEB_SAAS_TARGET_HOST",
            "web-saas-uat",
            "release_verify_supabase_",
            "target database already exists",
            "target is not empty",
            "row_fidelity_status",
            "not_verifiable_from_historical_checkpoint",
        ):
            self.assertIn(value, source)
        self.assertNotIn("pg_restore", source)
        self.assertNotIn("DROP DATABASE", source)

    def test_role_binds_archive_checksum_and_no_log_execution(self):
        tasks = ROLE_TASKS.read_text()
        self.assertIn("checksum_algorithm: sha256", tasks)
        self.assertIn("stat.mode == '0600'", tasks)
        self.assertIn("restore_supabase_sql_gzip_verify.sh", tasks)
        self.assertIn("no_log: true", tasks)

    def test_missing_input_fails_before_any_database_call(self):
        result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing required input", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
