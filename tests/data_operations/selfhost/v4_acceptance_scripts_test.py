#!/usr/bin/env python3
"""Exercise the V4 acceptance scripts with a fake Docker CLI, never a live service."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SELFHOST = ROOT / "tests/data_operations/selfhost"


class AcceptanceScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name)
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\nprintf '%s' \"${V4_MOCK_DOCKER_OUTPUT:-}\"\n")
        docker.chmod(0o755)

    def run_script(self, name, *, output="", **env):
        process_env = dict(os.environ)
        process_env.update({"PATH": f"{self.bin}:{process_env['PATH']}", "V4_MOCK_DOCKER_OUTPUT": output})
        process_env.update(env)
        return subprocess.run(["bash", str(SELFHOST / name)], env=process_env,
                              capture_output=True, text=True, timeout=10)

    def test_business_role_script_passes_and_fails_on_exact_grant_contract(self):
        passed = self.run_script("v4_business_role_acceptance.sh", output="account_user|t|t|t\n")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        failed = self.run_script("v4_business_role_acceptance.sh", output="account_user|f|t|t\n")
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("account_user|f", failed.stderr)
        wrong_role = self.run_script("v4_business_role_acceptance.sh", V4_BUSINESS_DB_ROLE="readonly_release")
        self.assertEqual(wrong_role.returncode, 2)

    def test_traffic_script_requires_node_and_two_recent_positive_buckets(self):
        missing_node = self.run_script("v4_traffic_chain_acceptance.sh")
        self.assertEqual(missing_node.returncode, 2)
        passed = self.run_script("v4_traffic_chain_acceptance.sh", output="2|536\n",
                                 V4_TRAFFIC_NODE_ID="fixture-node")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        no_traffic = self.run_script("v4_traffic_chain_acceptance.sh", output="1|0\n",
                                     V4_TRAFFIC_NODE_ID="fixture-node")
        self.assertEqual(no_traffic.returncode, 1)
        self.assertNotIn("fixture-node", no_traffic.stdout + no_traffic.stderr)

    def test_stripe_script_is_guarded_and_accepts_only_http_status_output(self):
        guarded = self.run_script("v4_stripe_sandbox_egress.sh")
        self.assertEqual(guarded.returncode, 2)
        reachable = self.run_script("v4_stripe_sandbox_egress.sh", output="401",
                                    V4_STRIPE_SANDBOX_ONLY="1")
        self.assertEqual(reachable.returncode, 0, reachable.stderr)
        invalid = self.run_script("v4_stripe_sandbox_egress.sh", output="curl error",
                                  V4_STRIPE_SANDBOX_ONLY="1")
        self.assertEqual(invalid.returncode, 1)

    def test_duplicate_inventory_cli_reads_fixture_and_rejects_collision(self):
        valid = [{"node_id": "jp", "transport": "tcp", "source": "host-a"},
                 {"node_id": "jp", "transport": "xhttp", "source": "host-a"}]
        duplicate = valid + [{"node_id": "jp", "transport": "tcp", "source": "host-b"}]
        script = SELFHOST / "v4_duplicate_producer_identity_test.py"
        for rows, expected in ((valid, 0), (duplicate, 1)):
            path = self.bin / "inventory.json"
            path.write_text(json.dumps(rows))
            env = dict(os.environ, V4_PRODUCER_INVENTORY=str(path))
            result = subprocess.run(["python3", str(script)], env=env, capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
