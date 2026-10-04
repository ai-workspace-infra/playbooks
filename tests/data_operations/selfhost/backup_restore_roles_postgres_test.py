#!/usr/bin/env python3
"""Real encrypted role-script round trip on a GitHub CI service, never UAT/PROD."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CONTAINER = os.environ.get("TEST_POSTGRES_CONTAINER", "")


@unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true" and re.fullmatch(r"[0-9a-f]{64}", CONTAINER),
                     "requires the disposable GitHub PostgreSQL service ID")
class BackupRestoreRolesPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which("docker")
        cls.fixture_database = "ci_role_fixture_" + os.environ["GITHUB_RUN_ID"]
        cls.temporary = tempfile.TemporaryDirectory()
        shim = Path(cls.temporary.name) / "docker"
        # Redirect only the canonical fixture container name to the CI service.
        shim.write_text("#!/bin/bash\nset -euo pipefail\nargs=(\"$@\")\nfor i in \"${!args[@]}\"; do\n"
                        " if [[ \"${args[$i]}\" == web-saas-postgresql ]]; then args[$i]=\"$TEST_POSTGRES_CONTAINER\"; fi\n"
                        " if [[ \"${args[$i]}\" == account ]]; then args[$i]=\"$TEST_FIXTURE_DATABASE\"; fi\n"
                        "done\nexec \"$TEST_REAL_DOCKER\" \"${args[@]}\"\n")
        shim.chmod(0o700)
        cls.env = dict(os.environ, PATH=cls.temporary.name + ":" + os.environ["PATH"], TEST_REAL_DOCKER=cls.docker,
                       TEST_FIXTURE_DATABASE=cls.fixture_database,
                       WEB_SAAS_DATABASE="account", WEB_SAAS_POSTGRES_CONTAINER="web-saas-postgresql",
                       WEB_SAAS_ENVIRONMENT="uat", WEB_SAAS_RUN_ID=os.environ["GITHUB_RUN_ID"],
                       WEB_SAAS_EXPECTED_VERSION="2026092801", WEB_SAAS_SOURCE_DATABASE_ID="ci-fixture-account",
                       WEB_SAAS_BASELINE_ID="ci-fixture-baseline", WEB_SAAS_AUTHORIZED_SUBSCRIPTION_SAMPLE_ID="ci-only-sample",
                       WEB_SAAS_BACKUP_PASSPHRASE="fixture-only-encryption-material-32-characters")
        cls.archive_dir = Path("/data/backups/web-saas/uat/ci-role-contract") / cls.env["WEB_SAAS_RUN_ID"]
        cls.archive_dir.mkdir(parents=True, mode=0o700)
        cls.env["WEB_SAAS_BACKUP_ROOT"] = str(cls.archive_dir)
        cls.psql("postgres", "CREATE DATABASE " + cls.fixture_database)
        cls.psql("account", """
          CREATE TABLE schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL);
          INSERT INTO schema_migrations VALUES (2026092801,false);
          CREATE TABLE users(id bigserial PRIMARY KEY, password_hash text, quota bigint);
          INSERT INTO users(password_hash,quota) VALUES ('original-password-fixture',5368709120),('admin-fixture',0);
          CREATE TABLE subscriptions(id bigserial PRIMARY KEY, user_id bigint, status text, amount bigint);
          INSERT INTO subscriptions(user_id,status,amount) VALUES(1,'active',2000);
          CREATE TABLE usage_ledger(id bigserial PRIMARY KEY, user_id bigint, bytes bigint);
          INSERT INTO usage_ledger(user_id,bytes) VALUES(1,1048576);
        """)

    @classmethod
    def psql(cls, database, sql):
        if database == 'account':
            database = cls.fixture_database
        result = subprocess.run([cls.docker, "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", database,
                                 "-XAtq", "-v", "ON_ERROR_STOP=1"], input=sql, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError("disposable PostgreSQL fixture command failed")
        return result.stdout.strip()

    def script(self, role, script, env=None):
        return subprocess.run(["bash", str(ROOT / "roles" / role / "files" / script)], env=env or self.env,
                              capture_output=True, text=True, timeout=90)

    def test_encrypted_round_trip_then_fail_closed_paths(self):
        backup = self.script("web_saas_data_backup", "create_encrypted_backup.sh")
        self.assertEqual(backup.returncode, 0, backup.stderr)
        receipt = json.loads(backup.stdout)
        archive = Path(receipt["archive_path"])
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b"original-password-fixture", archive.read_bytes())
        env = self.env | dict(WEB_SAAS_ARCHIVE_PATH=str(archive), WEB_SAAS_ARCHIVE_SHA256=receipt["archive_sha256"],
                             WEB_SAAS_EXPECTED_USERS=str(receipt["existing_users"]),
                             WEB_SAAS_EXPECTED_SUBSCRIPTIONS=str(receipt["subscriptions"]),
                             WEB_SAAS_EXPECTED_SCHEMA_SHA256=receipt["schema_sha256"],
                             WEB_SAAS_EXPECTED_DATA_SHA256=receipt["data_sha256"],
                             WEB_SAAS_DATABASE_SYSTEM_IDENTIFIER=receipt["database_system_identifier"])
        restored = self.script("web_saas_data_restore_verify", "restore_verify.sh", env)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertTrue(json.loads(restored.stdout)["restored_data_matches"])
        self.assertEqual(self.psql("postgres", "SELECT count(*) FROM pg_database WHERE datname='release_verify_" +
                                  env["WEB_SAAS_RUN_ID"] + "'"), "0")
        wrong_key = self.script("web_saas_data_restore_verify", "restore_verify.sh",
                                env | dict(WEB_SAAS_BACKUP_PASSPHRASE='incorrect-fixture-key-not-a-real-secret-32'))
        self.assertNotEqual(wrong_key.returncode, 0)
        self.assertEqual(self.psql("postgres", "SELECT count(*) FROM pg_database WHERE datname='release_verify_" +
                                  env["WEB_SAAS_RUN_ID"] + "'"), "0")
        # Same row counts, changed financial value: not a valid restore receipt.
        self.psql("account", "UPDATE subscriptions SET amount=9999")
        changed = self.script("web_saas_data_restore_verify", "restore_verify.sh", env)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("source rows or sequences changed", changed.stderr)
        self.psql("account", "UPDATE subscriptions SET amount=2000")
        # Existing isolated DB belongs to someone else: do not restore/drop it.
        isolated = "release_verify_" + env["WEB_SAAS_RUN_ID"]
        self.psql("postgres", "CREATE DATABASE " + isolated)
        occupied = self.script("web_saas_data_restore_verify", "restore_verify.sh", env)
        self.assertNotEqual(occupied.returncode, 0)
        self.assertIn("exists before this run", occupied.stderr)
        self.assertEqual(self.psql("postgres", "SELECT count(*) FROM pg_database WHERE datname='" + isolated + "'"), "1")
        self.psql("postgres", "DROP DATABASE " + isolated)  # fixture created here
        # A new checkpoint must refuse dirty state and an empty subscription.
        another = self.archive_dir.parent / (str(int(env["WEB_SAAS_RUN_ID"]) + 1))
        another.mkdir(mode=0o700)
        failure_env = self.env | dict(WEB_SAAS_BACKUP_ROOT=str(another), WEB_SAAS_RUN_ID=another.name)
        self.psql("account", "UPDATE schema_migrations SET dirty=true")
        dirty = self.script("web_saas_data_backup", "create_encrypted_backup.sh", failure_env)
        self.assertNotEqual(dirty.returncode, 0)
        self.assertFalse((another / "account.dump.enc").exists())
        self.psql("account", "UPDATE schema_migrations SET dirty=false; DELETE FROM subscriptions")
        empty = self.script("web_saas_data_backup", "create_encrypted_backup.sh", failure_env)
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("nonempty subscription sample", empty.stderr)
        self.assertFalse((another / "account.dump.enc").exists())
        self.assertEqual(self.psql("account", "SELECT quota FROM users WHERE id=1"), "5368709120")
        self.assertEqual(self.psql("account", "SELECT password_hash FROM users WHERE id=1"), "original-password-fixture")

    @classmethod
    def tearDownClass(cls):
        cls.psql("postgres", "DROP DATABASE " + cls.fixture_database)
        cls.temporary.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
