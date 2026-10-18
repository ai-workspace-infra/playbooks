"""Offline contract checks for the reusable cold-recovery role."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/web_saas_cold_recovery"
DOC = ROOT / "docs/data-operations/web-saas-prod-v2-cold-backup-disaster-recovery-whitepaper.zh.md"


class WebSaaSColdRecoveryRoleContractTests(unittest.TestCase):
    def test_entrypoint_exposes_backup_stage_and_isolated_restore(self) -> None:
        source = (ROOT / "web-saas-cold-recovery.yml").read_text()
        self.assertIn("web_saas_cold_recovery_source", source)
        self.assertIn("web_saas_cold_recovery_destination", source)
        self.assertIn("web_saas_cold_recovery_mode: backup", source)
        self.assertIn("web_saas_cold_recovery_mode: stage", source)
        self.assertIn("web_saas_cold_recovery_mode: restore_verify", source)

    def test_service_variant_is_explicit_and_receipt_bound(self) -> None:
        defaults = (ROLE / "defaults/main.yml").read_text()
        backup = (ROLE / "tasks/backup.yml").read_text()
        stage = (ROLE / "tasks/stage.yml").read_text()
        self.assertIn("web_saas_cold_recovery_service_variant", defaults)
        self.assertIn("'service_variant': web_saas_cold_recovery_service_variant", backup)
        self.assertIn("Source receipt belongs to a different web-saas service variant", stage)

    def test_primary_restore_is_fail_closed(self) -> None:
        source = (ROLE / "tasks/main.yml").read_text()
        self.assertIn("web_saas_cold_recovery_mode == 'restore_primary'", source)
        self.assertIn("that: false", source)
        self.assertIn("operator-only recovery runbook", source)

    def test_transport_only_moves_encrypted_archive_and_private_receipt(self) -> None:
        source = (ROLE / "tasks/backup.yml").read_text()
        stage = (ROLE / "tasks/stage.yml").read_text()
        self.assertIn("fetch:", source)
        self.assertIn("account.dump.enc", source)
        self.assertIn("mode: '0600'", source)
        self.assertIn("force: false", stage)
        self.assertIn("checksum == web_saas_cold_recovery_source_evidence.archive_sha256", stage)
        self.assertNotIn("web_saas_cold_recovery_passphrase", stage)

    def test_cross_machine_restore_does_not_require_source_connection(self) -> None:
        source = (ROOT / "roles/web_saas_data_restore_verify/files/restore_verify.sh").read_text()
        self.assertIn('WEB_SAAS_CROSS_MACHINE', source)
        self.assertIn("source_system_identity_comparison", source)
        self.assertIn('if [[ "${WEB_SAAS_CROSS_MACHINE:-false}" == true ]]; then', source)
        self.assertIn("restored database is not on the expected PostgreSQL instance", source)

    def test_current_prod_v2_schema_is_documented_without_secret_values(self) -> None:
        source = DOC.read_text()
        for value in (
            "accounts_controlled_migration_history",
            "subscription_records",
            "2026101703",
            "/data/backups/web-saas/prod-v2/full-snapshot-20261018/20261017T173532Z/",
            "kv/prod-v2/web-saas",
            "DB_RESTORE=PASS",
        ):
            self.assertIn(value, source)
        for forbidden in ("-----BEGIN", "password=", "token=", "postgresql://"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
