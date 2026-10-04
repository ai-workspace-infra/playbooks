import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_contract(role):
    path = ROOT / "roles" / role / "files" / "validate_contract.py"
    spec = importlib.util.spec_from_file_location(f"{role}_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BaselineManifestContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract("web_saas_data_baseline")
        self.manifest = {
            "source_db_id": "uat-supabase-01",
            "target_db_id": "uat-selfhost-01",
            "source_environment": "uat",
            "snapshot_id": "snapshot-20261004-01",
            "config_revision": "a" * 40,
            "approved_allowlist": ["users", "subscriptions"],
            "sanitization_policy": "policy-v3",
            "baseline_id": "uat-baseline-01",
            "users_sample_count": 12,
            "subscriptions_sample_count": 2,
            "approval": {
                "status": "approved",
                "approval_id": "approval-01",
                "approver": "release-owner",
                "approved_at": "2026-10-04T00:00:00Z",
                "scope": "UAT baseline evidence only",
                "source_db_id": "uat-supabase-01",
                "target_db_id": "uat-selfhost-01",
                "snapshot_id": "snapshot-20261004-01",
                "baseline_id": "uat-baseline-01",
            },
        }

    def validate(self, **overrides):
        manifest = {**self.manifest, **overrides}
        return self.contract.validate_baseline({"environment": "uat", "manifest": manifest})

    def test_records_manifest_metadata_without_data_actions(self):
        receipt = self.validate()
        self.assertEqual(receipt["status"], "manifest_validated")
        self.assertFalse(receipt["data_copied"])
        self.assertFalse(receipt["database_initialized"])
        self.assertFalse(receipt["seeded"])

    def test_rejects_empty_required_parameters_and_missing_approval(self):
        for field in ("source_db_id", "target_db_id", "snapshot_id", "config_revision", "sanitization_policy", "baseline_id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(**{field: ""})
        with self.assertRaises(ValueError):
            self.validate(approval={})
        with self.assertRaises(ValueError):
            self.validate(approved_allowlist=[])

    def test_rejects_prod_source_and_empty_samples(self):
        with self.assertRaisesRegex(ValueError, "PROD source"):
            self.validate(source_environment="prod")
        for field in ("users_sample_count", "subscriptions_sample_count"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(**{field: 0})

    def test_known_two_users_zero_subscriptions_is_not_a_valid_baseline(self):
        with self.assertRaisesRegex(ValueError, "subscriptions_sample_count"):
            self.validate(users_sample_count=2, subscriptions_sample_count=0)


class IncrementalMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract("web_saas_data_migration")
        self.request = {
            "environment": "uat",
            "candidate_sha256": "d" * 64,
            "accounts_source_revision": "e" * 40,
            "target_db_id": "uat-selfhost-01",
            "baseline_id": "uat-baseline-01",
            "container": "canonicalAccount",
            "expected_version": 2026092703,
            "observed_version": 2026092703,
            "observed_dirty": False,
            "target_version": 2026100101,
            "migration_checksum": "b" * 64,
            "advisory_lock": True,
            "single_migration_only": True,
            "lock_timeout_seconds": 15,
            "statement_timeout_seconds": 300,
            "approval": {
                "status": "approved",
                "approval_id": "approval-migration-01",
                "approver": "release-owner",
                "approved_at": "2026-10-04T00:00:00Z",
                "scope": "UAT schema migration only",
                "environment": "uat",
                "target_db_id": "uat-selfhost-01",
                "baseline_id": "uat-baseline-01",
                "candidate_sha256": "d" * 64,
                "accounts_source_revision": "e" * 40,
            },
        }
        self.baseline = {
            "status": "manifest_validated",
            "environment": "uat",
            "source_environment": "uat",
            "source_db_id": "uat-supabase-01",
            "baseline_id": "uat-baseline-01",
            "target_db_id": "uat-selfhost-01",
            "approved_allowlist": ["users", "subscriptions"],
            "sanitization_policy": "policy-v3",
            "snapshot_id": "snapshot-20261004-01",
            "config_revision": "a" * 40,
            "users_sample_count": 12,
            "subscriptions_sample_count": 2,
            "approval": {
                "status": "approved",
                "approval_id": "approval-01",
                "approver": "release-owner",
                "approved_at": "2026-10-04T00:00:00Z",
                "scope": "UAT baseline metadata",
                "source_db_id": "uat-supabase-01",
                "target_db_id": "uat-selfhost-01",
                "snapshot_id": "snapshot-20261004-01",
                "baseline_id": "uat-baseline-01",
            },
        }
        self.backup = {
            "phase": "database_backup_component",
            "status": "passed",
            "database": "account",
            "environment": "uat",
            "source_database_id": "uat-selfhost-01",
            "baseline_id": "uat-baseline-01",
            "checkpoint_id": "uat_41",
            "archive_path": "/data/backups/web-saas/uat/candidate/41/account.dump.enc",
            "archive_sha256": "c" * 64,
            "schema_version": 2026092703,
            "schema_sha256": "f" * 64,
            "data_sha256": "1" * 64,
            "database_system_identifier": "123456789",
            "existing_users": 12,
            "subscriptions": 2,
            "authorized_subscription_sample_id": "subscription-sample-01",
            "encrypted": True,
            "durable": True,
        }
        self.restore = {
            "phase": "database_restore_verify_component",
            "status": "passed",
            "database": "account",
            "environment": "uat",
            "source_database_id": "uat-selfhost-01",
            "baseline_id": "uat-baseline-01",
            "restore_database": "release_verify_41",
            "schema_version": 2026092703,
            "schema_sha256": "f" * 64,
            "data_sha256": "1" * 64,
            "database_system_identifier": "123456789",
            "existing_users": 12,
            "subscriptions": 2,
            "authorized_subscription_sample_id": "subscription-sample-01",
            "archive_sha256": "c" * 64,
            "isolated_restore_verified": True,
            "source_restore_schema_matches": True,
            "restored_data_matches": True,
            "business_acceptance": False,
        }

    def validate(self, request=None, baseline=None, backup=None, restore=None):
        return self.contract.validate({
            "environment": "uat",
            "request": self.request if request is None else request,
            "baseline_manifest": self.baseline if baseline is None else baseline,
            "backup_evidence": self.backup if backup is None else backup,
            "restore_evidence": self.restore if restore is None else restore,
        })

    def test_valid_inputs_still_block_until_execution_adapter_is_wired(self):
        result = self.validate()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["capability_supported"])
        self.assertEqual(result["reason_code"], "UNWIRED_SELFHOST_EXECUTION_ADAPTER")

    def test_rejects_dirty_or_non_exact_version_inputs(self):
        for changes in (
            {"observed_dirty": True},
            {"expected_version": 0, "target_version": 0},
            {"expected_version": 2026092703, "target_version": 2026092702},
            {"observed_version": 2026092702},
        ):
            with self.subTest(changes=changes):
                request = {**self.request, **changes}
                with self.assertRaises(ValueError):
                    self.validate(request=request)

    def test_rejects_missing_or_mismatched_agent_a_backup_restore_evidence(self):
        with self.assertRaisesRegex(ValueError, "backup evidence"):
            self.validate(backup={})
        bad_restore = {**self.restore, "isolated_restore_verified": False}
        with self.assertRaisesRegex(ValueError, "isolated_restore_verified"):
            self.validate(restore=bad_restore)
        bad_hash = {**self.restore, "archive_sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "archive_sha256"):
            self.validate(restore=bad_hash)
        bad_data = {**self.restore, "data_sha256": "3" * 64}
        with self.assertRaisesRegex(ValueError, "data_sha256"):
            self.validate(restore=bad_data)

    def test_rejects_unauthorized_baseline_source_and_noncanonical_container(self):
        with self.assertRaisesRegex(ValueError, "baseline source provenance"):
            self.validate(baseline={**self.baseline, "source_environment": "prod"})
        with self.assertRaisesRegex(ValueError, "canonicalAccount"):
            self.validate(request={**self.request, "container": "other"})

    def test_rejects_missing_migration_approval(self):
        with self.assertRaisesRegex(ValueError, "approval"):
            self.validate(request={**self.request, "approval": {}})

    def test_rejects_unbound_candidate_or_accounts_source(self):
        with self.assertRaisesRegex(ValueError, "candidate_sha256"):
            self.validate(request={**self.request, "candidate_sha256": ""})
        with self.assertRaisesRegex(ValueError, "accounts_source_revision"):
            self.validate(request={**self.request, "accounts_source_revision": "worktree"})

    def test_allows_in_place_same_target_identity(self):
        self.assertEqual(self.validate()["status"], "blocked")

    def test_rejects_missing_checksum_lock_and_timeouts(self):
        for field, value in (("migration_checksum", ""), ("advisory_lock", False), ("single_migration_only", False), ("lock_timeout_seconds", 0), ("statement_timeout_seconds", 3601)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(request={**self.request, field: value})

    def test_role_has_no_migration_command_or_secret_database_inputs(self):
        tasks = (ROOT / "roles/web_saas_data_migration/tasks/main.yml").read_text()
        validator = (ROOT / "roles/web_saas_data_migration/files/validate_contract.py").read_text()
        self.assertIn("UNWIRED_SELFHOST_EXECUTION_ADAPTER", validator)
        self.assertIn("ansible.builtin.fail", tasks)
        self.assertNotIn("docker", tasks)
        self.assertNotIn("psql", tasks)
        self.assertNotIn("migratectl", tasks)
        self.assertNotIn("TARGET_DSN", tasks)
        self.assertNotIn("init-schema", tasks)
        self.assertNotIn("force", tasks)


if __name__ == "__main__":
    unittest.main()
