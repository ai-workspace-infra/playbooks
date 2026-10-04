#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[3] / "scripts/data_operations/serverless/probe_uat_upgrade_readiness.py"
SPEC = importlib.util.spec_from_file_location("readiness", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.env = {"VAULT_ENV_PATH": "uat", "PROJECT_REF": "abcdefghijklmnopqrst",
                    "TARGET_DSN": "postgres://postgres.abcdefghijklmnopqrst:fixture-secret@aws-0-test.pooler.supabase.com:5432/postgres"}

    def test_credentials_are_environment_only(self):
        env = probe.connection_env(self.env)
        self.assertEqual(env["PGSSLMODE"], "require")
        self.assertEqual(env["PGPASSWORD"], "fixture-secret")
        with patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "1\n", "")) as run:
            self.assertEqual(probe.read_query("SELECT 1", env), "1")
        args, kwargs = run.call_args
        self.assertNotIn("fixture-secret", " ".join(args[0]))
        self.assertIn("BEGIN READ ONLY", args[0])
        self.assertEqual(kwargs["env"]["PGPASSWORD"], "fixture-secret")

    def test_prod_and_unknown_environment_refused(self):
        for value in ("prod", "sit", ""):
            with self.assertRaises(probe.Blocked):
                probe.connection_env({**self.env, "VAULT_ENV_PATH": value})

    def test_wrong_connection_and_insecure_tls_refused(self):
        original = self.env["TARGET_DSN"]
        for dsn in (original.replace(":5432", ":6543"), original.replace("postgres.abcdefghijklmnopqrst", "postgres.wrong"),
                    original.replace("/postgres", "/account"), original + "?sslmode=disable",
                    original + "?sslmode=require&sslmode=disable", original + "#fragment",
                    original.replace("aws-0-test.pooler.supabase.com", "production.example")):
            with self.subTest(dsn=dsn), self.assertRaises(probe.Blocked):
                probe.connection_env({**self.env, "TARGET_DSN": dsn})

    def test_psql_error_is_redacted(self):
        with patch.object(probe.subprocess, "run", side_effect=subprocess.CalledProcessError(1, [], stderr="fixture-secret")):
            with self.assertRaisesRegex(probe.Blocked, "database_connection_or_query_failed") as failure:
                probe.read_query("SELECT 1", self.env)
        self.assertNotIn("fixture-secret", str(failure.exception))

    def test_readonly_state_must_be_observed(self):
        with self.assertRaisesRegex(probe.Blocked, "read_only"):
            probe.collect(lambda _: json.dumps({"transaction_read_only": "off", "tables": []}))

    def observe(self, subscriptions=1, version=2026092801, dirty=False, tables=probe.TABLES, migration_rows=1):
        def query(sql):
            if "transaction_read_only" in sql:
                return json.dumps({"transaction_read_only": "on", "tables": list(tables)})
            if "json_build_object('version'" in sql:
                return json.dumps({"version": str(version), "dirty": dirty})
            if "count(*)" in sql:
                return str({"subscriptions": subscriptions, "schema_migrations": migration_rows}.get(sql.split("public.")[1], 2))
            raise AssertionError(sql)
        return probe.collect(query)

    def test_structural_success_still_blocks_business(self):
        report = probe.summarize(self.observe(), 2026092801)
        self.assertFalse(report["eligible_for_prod"])
        self.assertEqual(set(report["gates"].values()), {"blocked"})
        self.assertNotIn("upgrade_acceptance", report)

    def test_real_uat_shape_has_empty_subscription_and_version_blockers(self):
        report = probe.summarize(self.observe(subscriptions=0, version=2026092703), 2026092801)
        self.assertIn("nonempty_subscriptions_sample_required", report["blockers"])
        self.assertIn("artifact_migration_target_not_reached", report["blockers"])

    def test_missing_or_multiple_migration_rows_block(self):
        for rows in (0, 2):
            report = probe.summarize(self.observe(migration_rows=rows), 2026092801)
            self.assertIn("recognized_migration_tracking_required", report["blockers"])

    def test_missing_tables_block(self):
        report = probe.summarize(self.observe(tables=[]), 2026092801)
        self.assertIn("nonempty_users_sample_required", report["blockers"])
        self.assertIn("recognized_migration_tracking_required", report["blockers"])

    def test_dirty_or_later_version_not_accepted(self):
        self.assertIn("migration_dirty", probe.summarize(self.observe(dirty=True), 2026092801)["blockers"])
        self.assertIn("artifact_migration_target_not_reached", probe.summarize(self.observe(version=2026092802), 2026092801)["blockers"])


if __name__ == "__main__":
    unittest.main()
