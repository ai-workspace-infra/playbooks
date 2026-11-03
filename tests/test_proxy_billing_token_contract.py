"""Billing snapshot deploy entrypoints must use only the canonical Vault key."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TASK = "tasks/resolve-canonical-service-token.yml"


class ProxyBillingTokenContractTests(unittest.TestCase):
    def test_vault_fallback_reads_the_fixed_key_without_logging_or_mutation(self):
        tasks = yaml.safe_load((ROOT / CANONICAL_TASK).read_text())
        read = next(task for task in tasks if "ansible.builtin.command" in task)
        self.assertEqual(
            read["ansible.builtin.command"]["argv"],
            ["vault", "kv", "get", "-field=INTERNAL_SERVICE_TOKEN", "kv/prod/accounts/runtime"],
        )
        self.assertEqual(read["delegate_to"], "localhost")
        self.assertIs(read["changed_when"], False)
        self.assertIs(read["no_log"], True)
        self.assertIs(read["run_once"], True)
        self.assertIn("INTERNAL_SERVICE_TOKEN", str(read["when"]))

    def test_installer_token_is_used_without_vault_cli(self):
        token = "offline-installer-token-" + "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_vault = root / "vault"
            fake_vault.write_text("#!/bin/sh\nexit 73\n")
            fake_vault.chmod(0o700)
            play = root / "play.yml"
            play.write_text(
                "- hosts: localhost\n"
                "  gather_facts: false\n"
                "  tasks:\n"
                f"    - ansible.builtin.include_tasks: {ROOT / CANONICAL_TASK}\n"
            )
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                         INTERNAL_SERVICE_TOKEN=token, ANSIBLE_NOCOLOR="1"),
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_snapshot_entrypoints_use_the_canonical_resolver(self):
        agent = yaml.safe_load((ROOT / "deploy_observability_agent.yml").read_text())
        exporter = yaml.safe_load((ROOT / "deploy_xray_exporter.yml").read_text())[0]
        for play in (agent[0], agent[2], exporter):
            self.assertIn(CANONICAL_TASK, (ROOT / "deploy_observability_agent.yml").read_text()
                          if play in (agent[0], agent[2]) else (ROOT / "deploy_xray_exporter.yml").read_text())
            self.assertNotIn("lookup('ansible.builtin.env', 'INTERNAL_SERVICE_TOKEN')",
                             str(play.get("vars", {})))
        self.assertTrue(any("reviewed Proxy inventory hosts" in task.get("name", "")
                            for task in agent[2]["tasks"]))

    def test_vault_read_is_runtime_only_and_never_prints_the_value(self):
        token = "offline-test-token-" + "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_vault = root / "vault"
            fake_vault.write_text("#!/bin/sh\nprintf '%s' \"$TEST_CANONICAL_TOKEN\"\n")
            fake_vault.chmod(0o700)
            play = root / "play.yml"
            play.write_text(
                "- hosts: localhost\n"
                "  gather_facts: false\n"
                "  tasks:\n"
                f"    - ansible.builtin.include_tasks: {ROOT / CANONICAL_TASK}\n"
            )
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                         TEST_CANONICAL_TOKEN=token, ANSIBLE_NOCOLOR="1"),
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
