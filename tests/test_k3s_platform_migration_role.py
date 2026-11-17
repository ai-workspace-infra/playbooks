#!/usr/bin/env python3
"""Contract tests for the k3s_platform_migration Ansible role and CLI helpers."""

import os
import subprocess
import sys
import unittest

PLAYBOOKS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROLE_DIR = os.path.join(PLAYBOOKS_DIR, "roles", "vhosts", "k3s_platform_migration")
ROLE_FILES = os.path.join(ROLE_DIR, "files")
GITOPS_DIR = os.path.abspath(os.path.join(PLAYBOOKS_DIR, "..", "gitops-canary-samples"))

sys.path.insert(0, ROLE_FILES)

import audit_business_tables  # noqa: E402
import common  # noqa: E402
import isolate_source_cluster  # noqa: E402
import probe_cutover  # noqa: E402


class TestK3sPlatformMigrationRole(unittest.TestCase):
    def test_role_layout_exists(self):
        for rel in [
            "README.md",
            "defaults/main.yml",
            "tasks/main.yml",
            "tasks/audit.yml",
            "tasks/catchup.yml",
            "tasks/probe.yml",
            "tasks/isolate_source.yml",
            "files/common.py",
            "files/audit_business_tables.py",
            "files/catchup_sync.py",
            "files/probe_cutover.py",
            "files/isolate_source_cluster.py",
        ]:
            self.assertTrue(
                os.path.isfile(os.path.join(ROLE_DIR, rel)),
                f"Missing expected role file: {rel}",
            )

    def test_parse_host_manifests_without_hardcoding(self):
        src_path = os.path.join(
            GITOPS_DIR, "resources/svc.plus/prod/akamai/prod-k3s-v6.yaml"
        )
        tgt_path = os.path.join(
            GITOPS_DIR, "resources/svc.plus/prod/ulighthost/prod-k3s-v6-ulhost.yaml"
        )
        src = common.parse_host_manifest(src_path)
        tgt = common.parse_host_manifest(tgt_path)
        self.assertEqual(src["ansible_user"], "root")
        self.assertEqual(tgt["ansible_user"], "root")
        self.assertTrue(src["ssh_key_path"].startswith("kv/"))
        self.assertTrue(tgt["ssh_key_path"].startswith("kv/"))
        self.assertGreaterEqual(len(tgt["reserved_dns_aliases"]), 5)
        self.assertGreaterEqual(len(tgt["service_domains"]), 5)

        # Also verify stdlib fallback parser matches
        with open(tgt_path, "r", encoding="utf-8") as handle:
            fallback = common._fallback_parse_host_yaml(handle.read())
        fb_host = fallback["hosts"][0]
        self.assertEqual(fb_host["public_ipv4"], tgt["public_ipv4"])
        self.assertEqual(
            fb_host["host_vars"]["reserved_dns_aliases"],
            tgt["reserved_dns_aliases"],
        )

    def test_parse_tables_manifest_42_tables_and_sql_guard(self):
        tables_path = os.path.join(
            GITOPS_DIR, "operations/accounts-migration/target-business.yaml"
        )
        cfg = common.parse_tables_manifest(tables_path)
        self.assertEqual(len(cfg["tables"]), 42)
        self.assertEqual(cfg["database_name"], "account")
        sql = audit_business_tables.build_count_sql(cfg["tables"])
        self.assertEqual(sql.count("SELECT '"), 42)

        with self.assertRaises(ValueError):
            common.validate_sql_identifier("users; DROP TABLE users;--")

    def test_probe_paths_mapping(self):
        self.assertEqual(probe_cutover.probe_paths_for_domain("postgresql.svc.plus"), ("tcp", [5432]))
        self.assertEqual(probe_cutover.probe_paths_for_domain("vault.svc.plus"), ("https", ["/v1/sys/health"]))
        self.assertEqual(probe_cutover.probe_paths_for_domain("accounts.svc.plus"), ("https", ["/healthz", "/readyz"]))
        self.assertEqual(probe_cutover.probe_paths_for_domain("console.svc.plus"), ("https", ["/"]))
        self.assertEqual(probe_cutover.probe_paths_for_domain("xworktech.com"), ("https", ["/"]))

    def test_isolate_commands_preserve_safety_helpers(self):
        cmd = isolate_source_cluster.scale_zero_cmd(["web-saas-prod", "vault-prod"])
        self.assertIn("for ns in web-saas-prod vault-prod;", cmd)
        self.assertIn("--replicas=0", cmd)

    def test_playbook_syntax_check(self):
        playbook = os.path.join(PLAYBOOKS_DIR, "k3s_platform_migration_ops.yml")
        proc = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "--syntax-check", playbook],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
