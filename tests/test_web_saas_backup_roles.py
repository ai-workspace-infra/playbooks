"""Offline safety and parameter contract checks for web SaaS data roles."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "roles/web_saas_data_backup/files/create_encrypted_backup.sh"
RESTORE = ROOT / "roles/web_saas_data_restore_verify/files/restore_verify.sh"
BACKUP_TASKS = ROOT / "roles/web_saas_data_backup/tasks/main.yml"
RESTORE_TASKS = ROOT / "roles/web_saas_data_restore_verify/tasks/main.yml"
PREFLIGHT = ROOT / "roles/web_saas_data_preflight/tasks/main.yml"


class WebSaaSBackupRoleContractTests(unittest.TestCase):
    def run_rejected(self, script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )

    def test_backup_rejects_missing_runtime_inputs_before_database_access(self) -> None:
        result = self.run_rejected(BACKUP, {})
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing required input", result.stderr)

    def test_backup_keeps_zero_subscriptions_out_of_restore_gate(self) -> None:
        source = BACKUP.read_text()
        self.assertIn('[[ "$subscription_count" =~ ^[0-9]+$ ]] || fail', source)
        self.assertIn('"restore_gate_status": "passed"', source)
        self.assertIn('"g3_reason_code": None if int(subscriptions) > 0 else "NONEMPTY_SUBSCRIPTION_SAMPLE_REQUIRED"', source)

    def test_restore_rejects_missing_inputs_before_database_access(self) -> None:
        result = self.run_rejected(RESTORE, {})
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing required input", result.stderr)

    def test_restore_accepts_zero_expected_subscriptions_but_marks_g3_blocked(self) -> None:
        source = RESTORE.read_text()
        self.assertIn('[[ "$WEB_SAAS_EXPECTED_SUBSCRIPTIONS" =~ ^[0-9]+$ ]] || fail', source)
        self.assertIn('"restore_gate_status": "passed"', source)
        self.assertIn('"g3_status": "passed" if int(subscriptions) > 0 else "blocked"', source)

    def test_restore_rejects_invalid_expected_subscription_count(self) -> None:
        env = {
            "WEB_SAAS_ARCHIVE_PATH": "/data/backups/web-saas/uat/v1/7/account.dump.enc",
            "WEB_SAAS_DATABASE": "account",
            "WEB_SAAS_POSTGRES_CONTAINER": "web-saas-postgresql",
            "WEB_SAAS_ENVIRONMENT": "uat",
            "WEB_SAAS_RUN_ID": "7",
            "WEB_SAAS_EXPECTED_VERSION": "2026092801",
            "WEB_SAAS_SOURCE_DATABASE_ID": "prod-supabase-main",
            "WEB_SAAS_BASELINE_ID": "uat-baseline-7",
            "WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID": "approved-sample-1",
            "WEB_SAAS_EXPECTED_USERS": "2",
            "WEB_SAAS_EXPECTED_SUBSCRIPTIONS": "not-a-count",
            "WEB_SAAS_EXPECTED_SCHEMA_SHA256": "a" * 64,
            "WEB_SAAS_EXPECTED_DATA_SHA256": "c" * 64,
            "WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER": "1234567890123456789",
            "WEB_SAAS_ARCHIVE_SHA256": "b" * 64,
            "WEB_SAAS_BACKUP_PASSPHRASE": "x" * 32,
        }
        result = self.run_rejected(RESTORE, env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid subscription count", result.stderr)

    def test_preflight_separates_restore_readiness_from_g3(self) -> None:
        source = PREFLIGHT.read_text()
        self.assertIn("web_saas_data_preflight_source_database_id", source)
        self.assertIn("web_saas_data_preflight_baseline_id", source)
        self.assertIn("web_saas_data_preflight_authorized_subscription_sample_id", source)
        self.assertIn("web_saas_data_preflight_db_parts[1] is match('^[1-9][0-9]*$')", source)
        self.assertIn("web_saas_data_preflight_db_parts[2] is match('^[0-9]+$')", source)
        self.assertIn("g3_reason_code", source)
        self.assertIn("web_saas_data_preflight_db_parts[0] == '1:'", source)
        self.assertIn("g3_reason_code", source)
        self.assertIn("NONEMPTY_SUBSCRIPTION_SAMPLE_REQUIRED", source)

    def test_archive_and_restore_scripts_never_write_plaintext_or_cli_passwords(self) -> None:
        for script in (BACKUP, RESTORE):
            source = script.read_text()
            self.assertNotIn("psql -W", source)
            self.assertNotIn("PGPASSWORD=", source)
            self.assertNotIn(".pgpass", source)
            self.assertNotIn(".dump >", source)
        backup_source = BACKUP.read_text()
        self.assertIn("| openssl enc", backup_source)
        self.assertIn("-pass env:WEB_SAAS_BACKUP_PASSPHRASE", backup_source)

    def test_ansible_transfers_role_scripts_to_remote_before_execution(self) -> None:
        for task_file, script_name in (
            (BACKUP_TASKS, "create_encrypted_backup.sh"),
            (RESTORE_TASKS, "restore_verify.sh"),
        ):
            source = task_file.read_text()
            self.assertIn("ansible.builtin.script:", source)
            self.assertIn(f"cmd: '{{{{ role_path }}}}/files/{script_name}'", source)
            self.assertIn("executable: /bin/bash", source)
            self.assertNotIn(f"argv: [bash, '{{{{ role_path }}}}/files/{script_name}']", source)


if __name__ == "__main__":
    unittest.main()
