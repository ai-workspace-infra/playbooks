import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/docker/observability_server_operations/files/verify_post_dns_cutover.sh"


class PostDnsCutoverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        folder = Path(self.temporary.name)
        self.log = folder / "commands.log"
        stubs = {
            "ssh": '#!/bin/sh\nprintf "ssh %s\\n" "$*" >> "$TEST_LOG"\nexit "${TEST_SSH_EXIT:-0}"\n',
            "curl": '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$TEST_LOG"\nprintf "%s" "${TEST_CURL_CODE:-200}"\nexit "${TEST_CURL_EXIT:-0}"\n',
            "sleep": "#!/bin/sh\nexit 0\n",
        }
        for name, content in stubs.items():
            path = folder / name
            path.write_text(content)
            path.chmod(0o700)
        self.env = dict(
            os.environ, PATH=f"{folder}:{os.environ['PATH']}", TEST_LOG=str(self.log),
            POST_DNS_DOMAIN="metrics.example.invalid", POST_DNS_TARGET_IP="203.0.113.2",
            POST_DNS_HEALTH_PATH="/grafana/api/health", POST_DNS_RESTART_CADDY="true",
            POST_DNS_SSH_USER="root", SSH_PRIVATE_KEY_PATH=str(folder / "runtime-key"),
        )

    def run_script(self, **overrides):
        return subprocess.run(["bash", str(SCRIPT)], env=dict(self.env, **overrides),
                              capture_output=True, text=True)

    def test_caddy_refresh_precedes_verified_https_on_exact_origin(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertTrue(calls[0].startswith("ssh "))
        self.assertIn("root@203.0.113.2 systemctl restart caddy", calls[0])
        self.assertIn("--resolve metrics.example.invalid:443:203.0.113.2", calls[1])
        self.assertNotIn("--insecure", calls[1])

    def test_shared_origin_can_be_verified_without_a_restart_or_ssh_key(self):
        result = self.run_script(POST_DNS_RESTART_CADDY="false", SSH_PRIVATE_KEY_PATH="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ssh ", self.log.read_text())

    def test_caddy_failure_stops_health_verification(self):
        result = self.run_script(TEST_SSH_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("curl ", self.log.read_text())

    def test_http_or_transport_failure_never_reports_healthy(self):
        for overrides in ({"TEST_CURL_CODE": "503"}, {"TEST_CURL_EXIT": "60"}):
            with self.subTest(overrides=overrides):
                result = self.run_script(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must restore DNS", result.stderr)

    def test_missing_target_and_invalid_restart_selection_fail_before_ssh(self):
        for overrides in ({"POST_DNS_TARGET_IP": ""}, {"POST_DNS_RESTART_CADDY": "maybe"}):
            with self.subTest(overrides=overrides):
                result = self.run_script(**overrides)
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_actual_uat_role_calls_the_post_cutover_executor(self):
        env = dict(self.env, DNS_RECORD_NAME=self.env["POST_DNS_DOMAIN"],
                   TARGET_IP=self.env["POST_DNS_TARGET_IP"], RESTART_CADDY="false")
        result = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", "observability_operations.yml",
             "-e", "observability_operations_environment=uat", "-e", "observability_operation=post_dns_cutover"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("post_dns_cutover.yml", result.stdout)
        self.assertIn("curl ", self.log.read_text())
        self.assertNotIn("ssh ", self.log.read_text())

    def test_actual_role_rejects_prod_before_any_host_or_health_operation(self):
        result = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", "observability_operations.yml",
             "-e", "observability_operations_environment=prod", "-e", "observability_operation=post_dns_cutover"],
            cwd=ROOT, env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
