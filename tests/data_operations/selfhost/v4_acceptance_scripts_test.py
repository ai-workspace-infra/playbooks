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
        hostname = self.bin / "hostname"
        hostname.write_text("#!/bin/sh\nprintf '%s\\n' \"${V4_MOCK_HOSTNAME:-web-saas-prod-v5}\"\n")
        hostname.chmod(0o755)

    def run_script(self, name, *, output="", **env):
        process_env = dict(os.environ)
        process_env.update({"PATH": f"{self.bin}:{process_env['PATH']}", "V4_MOCK_DOCKER_OUTPUT": output})
        process_env.update(env)
        return subprocess.run(["bash", str(SELFHOST / name)], env=process_env,
                              capture_output=True, text=True, timeout=10)

    def test_business_role_script_passes_and_fails_on_exact_grant_contract(self):
        target = {"GENERATION_TARGET_GENERATION": "prod-v5",
                  "GENERATION_POSTGRES_CONTAINER": "web-saas-postgresql-v5-fixture"}
        guarded = self.run_script("v4_business_role_acceptance.sh")
        self.assertEqual(guarded.returncode, 2)
        passed = self.run_script("v4_business_role_acceptance.sh", output="account_user|t|t|t\n", **target)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        wrong_host = self.run_script("v4_business_role_acceptance.sh", **target,
                                     V4_MOCK_HOSTNAME="web-saas-prod-v4")
        self.assertEqual(wrong_host.returncode, 2)
        failed = self.run_script("v4_business_role_acceptance.sh", output="account_user|f|t|t\n", **target)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("account_user|f", failed.stderr)
        wrong_role = self.run_script("v4_business_role_acceptance.sh", **target,
                                     GENERATION_BUSINESS_DB_ROLE="readonly_release")
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

    def test_traffic_script_supports_zero_traffic_mode_and_sanitized_receipt_json(self):
        receipt_path = self.bin / "receipt.json"
        zero_passed = self.run_script(
            "v4_traffic_chain_acceptance.sh",
            output="2|0\n",
            V4_TRAFFIC_NODE_ID="fixture-node-secret",
            V4_ALLOW_ZERO_TRAFFIC="1",
            V4_RECEIPT_JSON=str(receipt_path),
            V4_CASE_ID="case-zero-traffic-01",
        )
        self.assertEqual(zero_passed.returncode, 0, zero_passed.stderr)
        self.assertIn("PASS (ZERO_TRAFFIC): recent buckets=2; bytes=0", zero_passed.stdout)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["case_id"], "case-zero-traffic-01")
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["traffic_state"], "zero_traffic")
        self.assertEqual(receipt["bucket_count"], 2)
        self.assertEqual(receipt["byte_sum"], 0)
        self.assertNotIn("fixture-node-secret", json.dumps(receipt))

        insufficient_buckets = self.run_script(
            "v4_traffic_chain_acceptance.sh",
            output="1|0\n",
            V4_TRAFFIC_NODE_ID="fixture-node-secret",
            V4_ALLOW_ZERO_TRAFFIC="1",
            V4_RECEIPT_JSON=str(receipt_path),
        )
        self.assertEqual(insufficient_buckets.returncode, 1)
        failed_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(failed_receipt["status"], "FAIL")
        self.assertEqual(failed_receipt["traffic_state"], "insufficient_buckets")
        self.assertNotIn("fixture-node-secret", json.dumps(failed_receipt))

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
