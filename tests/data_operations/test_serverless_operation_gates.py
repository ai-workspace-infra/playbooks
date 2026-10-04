"""Offline contract tests for Serverless database operation and restore gates."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SERVERLESS = ROOT / "scripts/data_operations/serverless"
VALIDATOR = SERVERLESS / "validate_serverless_operation.py"
sys.path.insert(0, str(SERVERLESS))
import verify_checkpoint_receipt


REPOSITORY = "ai-workspace-infra/platform-ops-toolkit"
RELEASE_TAG = "daily-build-2026.10.04-r3"
DIGEST = "a" * 64


class ServerlessOperationGateTests(unittest.TestCase):
    def invoke(self, environment, operation, release_tag="", config=None, correlation="offline-check"):
        env = dict(os.environ)
        env.update({
            "REQUESTED_ENVIRONMENT": environment,
            "REQUESTED_OPERATION": operation,
            "RELEASE_TAG": release_tag,
            "CONFIG_JSON": json.dumps(config or {}),
            "CORRELATION_ID": correlation,
            "GITHUB_REPOSITORY": REPOSITORY,
        })
        return subprocess.run([sys.executable, str(VALIDATOR)], env=env,
                              text=True, capture_output=True, check=False)

    def locator(self):
        return {"checkpoint_run_id": "123", "expected_schema_version": "2026092703", "backup_receipt": {
            "source_repository": REPOSITORY, "source_run_id": "123", "artifact_id": "456",
            "artifact_name": "serverless-database-receipt", "backup_sha256": DIGEST}}

    def test_prod_probe_and_checkpoint_are_allowed(self):
        self.assertEqual(self.invoke("prod", "probe").returncode, 0)
        self.assertEqual(self.invoke("prod", "checkpoint", "prod-r42",
                                     {"require_durable_checkpoint": False}).returncode, 0)

    def test_prod_baseline_and_migration_are_rejected(self):
        for operation in ("baseline", "migrate"):
            with self.subTest(operation=operation):
                result = self.invoke("prod", operation, RELEASE_TAG, self.locator())
                self.assertNotEqual(result.returncode, 0)

    def test_checkpoint_run_id_alone_fails_before_credentials(self):
        result = self.invoke("uat", "baseline", RELEASE_TAG, {"checkpoint_run_id": "123"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup_receipt", result.stderr)
        self.assertIn("before credentials", result.stderr)

    def test_init_schema_is_explicitly_retired(self):
        result = self.invoke("uat", "init-schema")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init-schema is retired", result.stderr)

    def test_migration_uses_caller_schema_field_names_and_reviewed_repair_pin(self):
        config = self.locator() | {
            "repair_schema": True,
            "accounts_source_sha": "c2c343fe2c91bb31e7f7f9b4fa60512a66e2b4c9",
            "expected_schema_version": "2026092703",
            "target_schema_version": "2026092801",
            "migration_sha256": "d066e223641b4eccbb65a00dce70f717b6dce02491d1d54edc1099baf2071433",
        }
        result = self.invoke("uat", "migrate", RELEASE_TAG, config)
        self.assertEqual(result.returncode, 0, result.stderr)
        wrong_name = dict(config, expected_version="2026092703")
        wrong_name.pop("expected_schema_version")
        result = self.invoke("uat", "migrate", RELEASE_TAG, wrong_name)
        self.assertNotEqual(result.returncode, 0)

    def test_durable_checkpoint_requires_immutable_uat_tag(self):
        config = {"require_durable_checkpoint": True}
        self.assertNotEqual(self.invoke("uat", "checkpoint", "adhoc", config).returncode, 0)
        self.assertEqual(self.invoke("uat", "checkpoint", RELEASE_TAG, config).returncode, 0)

    def trusted_run(self):
        return {"id": 123, "head_repository": {"full_name": REPOSITORY},
                "path": ".github/workflows/environment-data-operations.yml",
                "head_branch": "main", "head_sha": "b" * 40,
                "status": "completed", "conclusion": "success"}

    def trusted_artifacts(self, size=400):
        return {"artifacts": [{"id": 456, "name": "serverless-database-receipt",
                               "expired": False, "size_in_bytes": size}]}

    def verify_env(self, root, output):
        config = self.locator()
        env = {"GH_TOKEN": "offline-token", "TRUSTED_REPOSITORY": REPOSITORY,
               "VAULT_ENV_PATH": "uat", "RELEASE_TAG": RELEASE_TAG,
               "TRUSTED_CALLER_SHA": "b" * 40, "REQUESTED_OPERATION": "migrate",
               "CURRENT_RUN_ID": "123",
               "CONFIG_JSON": json.dumps(config), "GITHUB_OUTPUT": str(output)}
        return patch.dict(os.environ, env), config

    def receipt(self):
        return {"schema": "serverless-data-backup-receipt/v1",
                "source_repository": REPOSITORY, "source_run_id": 123,
                "caller_sha": "b" * 40,
                "environment": "uat", "release_tag": RELEASE_TAG,
                "backup_sha256": DIGEST, "encrypted": True,
                "restore_verified": True, "host_environment": "uat",
                "host_id": "uat-db-01", "restore_host_id": "uat-db-01",
                "backup_path": f"/data/backups/web-saas/uat/{RELEASE_TAG}/123/release.enc",
                "database_identity": "uat-primary/postgres",
                "restore_database_identity": "uat-primary/isolated-restore-123",
                "sample_counts": {"users": 23, "subscriptions": 7},
                "schema_version": "2026092703", "schema_dirty": False,
                "verified_at": "2026-10-04T00:00:00Z",
                "evidence_ref": "host-receipt://restore/123"}

    def test_trusted_sanitized_receipt_passes_without_backup_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_root = root / "receipt"
            artifact_root.mkdir()
            (artifact_root / "backup-receipt.json").write_text(json.dumps(self.receipt()))
            output = root / "output"
            env_patch, _ = self.verify_env(root, output)
            with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                    side_effect=[self.trusted_run(), self.trusted_artifacts()]), patch.object(sys, "argv", [
                        "verify_checkpoint_receipt.py", str(artifact_root)]):
                verify_checkpoint_receipt.main()
            self.assertIn("checkpoint_verified=true", output.read_text())
            self.assertEqual([path.name for path in artifact_root.iterdir()], ["backup-receipt.json"])

    def test_receipt_missing_or_boolean_only_fails_closed(self):
        for content in (None, {"restore_verified": True, "backup_sha256": DIGEST}):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if content is not None:
                    (root / "backup-receipt.json").write_text(json.dumps(content))
                env_patch, _ = self.verify_env(root, root / "output")
                with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                        side_effect=[self.trusted_run(), self.trusted_artifacts()]), patch.object(sys, "argv", [
                            "verify_checkpoint_receipt.py", str(root)]):
                    with self.assertRaises(ValueError):
                        verify_checkpoint_receipt.main()

    def test_artifact_with_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "backup-receipt.json").write_text(json.dumps(self.receipt()))
            (root / "database.sql.enc").write_bytes(b"not allowed")
            env_patch, _ = self.verify_env(root, root / "output")
            with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                    side_effect=[self.trusted_run(), self.trusted_artifacts()]), patch.object(sys, "argv", [
                        "verify_checkpoint_receipt.py", str(root)]):
                with self.assertRaisesRegex(ValueError, "only the sanitized"):
                    verify_checkpoint_receipt.main()

    def test_preflight_rejects_large_artifact_before_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_patch, _ = self.verify_env(Path(temporary), Path(temporary) / "output")
            with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                    side_effect=[self.trusted_run(), self.trusted_artifacts(size=200000)]), patch.object(sys, "argv", [
                        "verify_checkpoint_receipt.py", "--preflight"]):
                with self.assertRaisesRegex(ValueError, "metadata-only"):
                    verify_checkpoint_receipt.main()

    def test_preflight_rejects_unpinned_or_non_main_source_run(self):
        for field, value in (("head_sha", "c" * 40), ("head_branch", "feature/backup"),
                             ("path", ".github/workflows/serverless-orchestrator.yml")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                run = self.trusted_run()
                run[field] = value
                env_patch, _ = self.verify_env(Path(temporary), Path(temporary) / "output")
                with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                        side_effect=[run]), patch.object(sys, "argv", [
                            "verify_checkpoint_receipt.py", "--preflight"]):
                    with self.assertRaisesRegex(ValueError, "pinned caller SHA"):
                        verify_checkpoint_receipt.main()

    def test_receipt_mismatches_fail_for_env_tag_run_path_database_and_version(self):
        mutations = {
            "environment": lambda r: r.update(environment="prod"),
            "release tag": lambda r: r.update(release_tag="another-release"),
            "source run": lambda r: r.update(source_run_id=999),
            "backup path": lambda r: r.update(backup_path="/data/backups/web-saas/prod/wrong/999/db.enc"),
            "database identity": lambda r: r.update(restore_database_identity=r["database_identity"]),
            "nonempty samples": lambda r: r.update(sample_counts={"users": 0, "subscriptions": 1}),
            "clean expected version": lambda r: r.update(schema_version="2026092801"),
            "schema dirty": lambda r: r.update(schema_dirty=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                receipt = self.receipt()
                mutate(receipt)
                (root / "backup-receipt.json").write_text(json.dumps(receipt))
                env_patch, _ = self.verify_env(root, root / "output")
                with env_patch, patch.object(verify_checkpoint_receipt, "api_json",
                        side_effect=[self.trusted_run(), self.trusted_artifacts()]), patch.object(sys, "argv", [
                            "verify_checkpoint_receipt.py", str(root)]):
                    with self.assertRaises(ValueError):
                        verify_checkpoint_receipt.main()


if __name__ == "__main__":
    unittest.main()
