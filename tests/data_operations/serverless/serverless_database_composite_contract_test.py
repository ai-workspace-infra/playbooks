#!/usr/bin/env python3
"""Offline contracts for the reusable Serverless database composite action."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[3]
ACTION_PATH = ROOT / ".github/actions/serverless-database-operation/action.yml"
WORKFLOW_PATH = ROOT / ".github/workflows/serverless-database-operations.yml"
RECEIPT_SCRIPT = ROOT / "scripts/data_operations/serverless/write_operation_receipt.py"
spec = importlib.util.spec_from_file_location("operation_receipt", RECEIPT_SCRIPT)
operation_receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operation_receipt)


class ServerlessDatabaseCompositeContractTests(unittest.TestCase):
    def setUp(self):
        self.action = yaml.safe_load(ACTION_PATH.read_text())
        self.workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        self.steps = self.action["runs"]["steps"]
        self.names = [step.get("name") for step in self.steps]

    def test_owner_workflow_calls_local_composite_inside_environment_job(self):
        jobs = self.workflow["jobs"]
        job = jobs["database_operation"]
        trigger = self.workflow.get("on", self.workflow.get(True, {}))
        self.assertEqual(set(trigger), {"workflow_call"})
        self.assertEqual(job["environment"], "${{ inputs.environment }}")
        self.assertEqual(job["steps"][0]["with"]["ref"], "${{ job.workflow_sha }}")
        self.assertEqual(job["steps"][1]["uses"], "./playbooks-owner/.github/actions/serverless-database-operation")
        self.assertEqual(job["steps"][1]["with"]["owner_sha"], "${{ job.workflow_sha }}")
        self.assertEqual(self.workflow["permissions"]["id-token"], "write")
        self.assertEqual(self.workflow["permissions"]["actions"], "read")
        self.assertEqual(job["outputs"]["data_run_id"], "${{ steps.serverless_database.outputs.data_run_id }}")
        self.assertEqual(job["outputs"]["data_run_attempt"], "${{ steps.serverless_database.outputs.data_run_attempt }}")

    def test_restore_evidence_is_verified_before_oidc_credentials(self):
        preflight = self.names.index("Preflight trusted receipt run and metadata artifact before download")
        download = self.names.index("Download sanitized trusted checkpoint receipt")
        verify = self.names.index("Verify trusted source run and sanitized isolated restore receipt")
        vault = self.names.index("Load database credentials from Vault with GitHub OIDC")
        self.assertLess(preflight, download)
        self.assertLess(download, verify)
        self.assertLess(verify, vault)
        self.assertIn("verify_checkpoint_receipt.py --preflight", self.steps[preflight]["run"])

    def test_data_mutations_remain_explicit_uat_only_and_sit_is_rejected(self):
        validator = (ROOT / "scripts/data_operations/serverless/validate_serverless_operation.py").read_text()
        self.assertIn('environment in {"uat", "prod"}', validator)
        self.assertIn('if operation in {"baseline", "migrate"}:', validator)
        self.assertIn('environment == "uat"', validator)
        self.assertIn("Only uat or prod", self.action["inputs"]["environment"]["description"])
        self.assertIn('operation not in {"baseline", "migrate"} or environment == "uat"',
                      RECEIPT_SCRIPT.read_text())

    def test_action_never_exports_database_credentials(self):
        output_names = set(self.action.get("outputs", {}))
        self.assertTrue(output_names.isdisjoint({"TARGET_DSN", "PROJECT_REF", "DATABASE_URL", "SUPABASE_URL"}))
        self.assertNotIn("TARGET_DSN", json.dumps(self.action.get("outputs", {})))

    def test_owner_and_environment_are_checked_before_credentials(self):
        self.assertLess(self.names.index("Verify the owner checkout before credentials"),
                        self.names.index("Load database credentials from Vault with GitHub OIDC"))
        for name in ("Probe Accounts schema without writes", "Adopt reviewed UAT Accounts baseline",
                     "Capture private before-repair evidence", "Apply reviewed Accounts incremental migration",
                     "Verify retained data and serving health after repair"):
            step = self.steps[self.names.index(name)]
            self.assertEqual(step["env"]["VAULT_ENV_PATH"], "${{ inputs.environment }}")

    def test_owner_checkout_rejects_wrong_or_mutable_ref(self):
        expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                  capture_output=True, text=True).stdout.strip()
        script = ROOT / "scripts/data_operations/serverless/verify_owner_checkout.py"
        for value, code in ((expected, 0), ("f" * 40, 2), ("main", 2)):
            with self.subTest(value=value):
                result = subprocess.run([sys.executable, str(script)], cwd=ROOT,
                                        env={**os.environ, "EXPECTED_OWNER_SHA": value},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, code)

    def test_operation_receipt_binds_run_attempt_and_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt_path = Path(temp) / "receipt.json"
            output_path = Path(temp) / "github-output"
            env = {
                "OPERATION_ENVIRONMENT": "uat",
                "OPERATION_NAME": "probe",
                "OPERATION_RESULT": "success",
                "CORRELATION_ID": "serverless-run-42",
                "RELEASE_TAG": "daily-build-2026.10.27-r2",
                "CALLER_REPOSITORY": "ai-workspace-infra/platform-ops-toolkit",
                "CALLER_SHA": "a" * 40,
                "CALLER_WORKFLOW_REF": "ai-workspace-infra/platform-ops-toolkit/.github/workflows/serverless-deploy-data.yml@refs/heads/main",
                "CALLER_RUN_ID": "4567",
                "CALLER_RUN_ATTEMPT": "2",
                "OWNER_ACTION_REF": "e" * 40,
                "RECEIPT_PATH": str(receipt_path),
                "GITHUB_OUTPUT": str(output_path),
            }
            with patch.dict(os.environ, env, clear=False):
                operation_receipt.main()
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["caller"]["run_id"], 4567)
            self.assertEqual(receipt["caller"]["run_attempt"], 2)
            self.assertEqual(receipt["environment"], "uat")
            self.assertEqual(receipt["operation"], "probe")
            self.assertEqual(receipt["result"], "success")
            self.assertNotIn("TARGET_DSN", receipt)
            self.assertNotIn("password", receipt)
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
            self.assertIn(f"receipt_sha256={hashlib.sha256(receipt_path.read_bytes()).hexdigest()}", output_path.read_text())
            self.assertIn("data_run_attempt=2", output_path.read_text())

    def test_operation_receipt_rejects_sit_and_prod_migration(self):
        base = {
            "OPERATION_ENVIRONMENT": "sit",
            "OPERATION_NAME": "probe",
            "OPERATION_RESULT": "success",
            "CORRELATION_ID": "serverless-run-42",
            "RELEASE_TAG": "daily-build-2026.10.27-r2",
            "CALLER_REPOSITORY": "ai-workspace-infra/platform-ops-toolkit",
            "CALLER_SHA": "a" * 40,
            "CALLER_WORKFLOW_REF": "ai-workspace-infra/platform-ops-toolkit/.github/workflows/serverless-deploy-data.yml@refs/heads/main",
            "CALLER_RUN_ID": "4567",
            "CALLER_RUN_ATTEMPT": "1",
            "OWNER_ACTION_REF": "e" * 40,
        }
        for override in ({}, {"OPERATION_ENVIRONMENT": "prod", "OPERATION_NAME": "migrate"}):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temp:
                env = base | override | {
                    "RECEIPT_PATH": str(Path(temp) / "receipt.json"),
                    "GITHUB_OUTPUT": str(Path(temp) / "output"),
                }
                with patch.dict(os.environ, env, clear=False), self.assertRaises(ValueError):
                    operation_receipt.main()


if __name__ == "__main__":
    unittest.main()
