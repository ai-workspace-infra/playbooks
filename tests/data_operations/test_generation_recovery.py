"""Security contracts for new-generation restore; no production connection."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("restore", ROOT / "roles/web_saas_generation_recovery/files/restore_new_generation.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class GenerationRecoveryTests(unittest.TestCase):
    def contract(self, generation="prod-v3", source="prod-v2", hostname="web-saas-prod-v3", digest="a"*64):
        restore.validate_contract(generation, source, digest, "2026101703",
                                  {"users": 30, "subscription_records": 12, "billing_ledger": 12218}, hostname)

    def test_generation_n_is_supported(self):
        self.contract()
        self.contract("prod-v12", "prod-v11", "web-saas-prod-v12")

    def test_source_cannot_be_target(self):
        with self.assertRaises(ValueError):
            self.contract(source="prod-v3")

    def test_actual_source_host_is_refused(self):
        with self.assertRaises(ValueError):
            self.contract(hostname="web-saas-prod-v2")

    def test_stable_prod_profile_is_refused(self):
        with self.assertRaises(ValueError):
            self.contract("prod", "prod-v2", "web-saas-prod")

    def test_invalid_archive_identity_is_refused(self):
        with self.assertRaises(ValueError):
            self.contract(digest="../account.dump")

    def test_historical_archive_reuses_default_public(self):
        self.assertFalse(restore.archive_creates_public("2; 3079 16390 EXTENSION - pgcrypto\n"))
        self.assertTrue(restore.archive_creates_public("5; 2615 2200 SCHEMA - public postgres\n"))

    def test_forced_rls_cannot_be_weakened_by_owner_mapping(self):
        with mock.patch.object(restore, "pg", side_effect=["t", "1"]) as query:
            with self.assertRaises(ValueError):
                restore.align_rls_ownership()
            self.assertEqual(query.call_count, 2)

    def test_owner_mapping_rejects_privileged_application_role(self):
        with mock.patch.object(restore, "pg", side_effect=["f", "0"]) as query:
            with self.assertRaises(ValueError):
                restore.align_rls_ownership()
            self.assertEqual(query.call_count, 2)

    def test_no_implicit_cleanup_or_production_secret_copy(self):
        text = (ROOT / "roles/web_saas_generation_recovery/files/restore_new_generation.py").read_text()
        self.assertNotIn("DROP DATABASE", text)
        self.assertNotIn("--clean", text)
        self.assertNotIn("CASCADE", text)
        self.assertIn("account already exists without a completed checkpoint", text)
        template = (ROOT / "roles/web_saas_generation_recovery/templates/secrets.env.j2").read_text()
        self.assertIn("STRIPE_SECRET_KEY=\n", template)
        self.assertIn("DATABASE_BACKGROUND_WRITERS=false", template)

    def test_role_defends_cmdb_disk_and_mutation_contract(self):
        tasks = yaml.safe_load((ROOT / "roles/web_saas_generation_recovery/tasks/main.yml").read_text())
        guard = next(t for t in tasks if t.get("name") == "Require an exact isolated generation from the IaC inventory")
        assertions = " ".join(guard["ansible.builtin.assert"]["that"])
        for check in ("cmdb_name", "web_saas_recovery", "source.generation", "public_dns_cutover",
                      "application_database_read_only", "vault_raft_restore"):
            self.assertIn(check, assertions)
        self.assertIn("docker_bootstrap_host", (ROOT / "roles/vhosts/docker/meta/main.yml").read_text())


if __name__ == "__main__":
    unittest.main()
