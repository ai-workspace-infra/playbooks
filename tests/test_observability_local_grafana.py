"""Run the actual independent role against a loopback-only HTTP fixture."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/docker/observability_server_operations"


class LocalGrafanaTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = 200
        self.body = b'{"database":"ok", "private_detail":"never-print-this"}'
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.calls.append(self.path)
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                self.wfile.write(fixture.body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.inventory = Path(self.temporary.name) / "inventory.ini"
        self.inventory.write_text(
            "[observability_hosts]\nfixture-node ansible_connection=local "
            "ansible_host=127.0.0.1\n"
        )

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_role(self, **overrides):
        variables = {
            "observability_operations_environment": "uat",
            "observability_operation": "verify_local_grafana",
            "observability_local_health_host": "fixture-node",
            "observability_local_grafana_port": self.server.server_port,
            "observability_local_health_retries": 1,
            "observability_local_health_delay": 0,
            "observability_local_health_timeout": 1,
            "ansible_become": False,
        }
        variables.update(overrides)
        return subprocess.run(
            ["ansible-playbook", "-i", str(self.inventory), "-c", "local",
             "observability_operations.yml", "-e", json.dumps(variables)],
            cwd=ROOT, env=dict(os.environ, ANSIBLE_NOCOLOR="1"),
            capture_output=True, text=True, timeout=60,
        )

    def test_healthy_exact_host_runs_independently_and_redacts_response(self):
        result = self.run_role()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls, ["/api/health"])
        self.assertIn("fixture-node", result.stdout)
        self.assertIn('"status": "passed"', result.stdout)
        self.assertIn("changed=0", result.stdout)
        self.assertNotIn("never-print-this", result.stdout + result.stderr)
        self.assertNotIn("docker/observability-server", result.stdout)

    def test_unhealthy_database_fails_without_passing_evidence(self):
        self.body = b'{"database":"failed", "private_detail":"never-print-this"}'
        result = self.run_role()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.calls)
        self.assertNotIn('"status": "passed"', result.stdout)
        self.assertNotIn("never-print-this", result.stdout + result.stderr)

    def test_http_redirect_and_invalid_json_fail_closed(self):
        for status, body in ((503, b'{}'), (302, b'{}'), (200, b'not-json'), (200, b'[]')):
            with self.subTest(status=status, body=body):
                self.calls.clear()
                self.status, self.body = status, body
                result = self.run_role()
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(self.calls)
                self.assertEqual(set(self.calls), {"/api/health"})
                self.assertNotIn('"status": "passed"', result.stdout)

    def test_unreachable_service_fails_without_passing_evidence(self):
        import socket

        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            port = unavailable.getsockname()[1]
            result = self.run_role(observability_local_grafana_port=port)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls, [])
        self.assertNotIn('"status": "passed"', result.stdout)

    def test_wrong_environment_or_missing_target_fails_before_io(self):
        for overrides in (
            {"observability_operations_environment": "prod"},
            {"observability_local_health_host": ""},
            {"observability_local_health_host": "missing-node"},
            {"observability_local_health_host": "all"},
            {"observability_local_grafana_port": "3030/path"},
            {"observability_local_health_retries": 0},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_role(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls, [])

    def test_operation_is_read_only_delegated_and_does_not_import_deploy(self):
        tasks = yaml.safe_load((ROLE / "tasks/verify_local_grafana.yml").read_text())
        probe = next(task for task in tasks if "ansible.builtin.uri" in task)
        self.assertFalse(probe["changed_when"])
        self.assertTrue(probe["no_log"])
        self.assertEqual(probe["delegate_to"], "{{ observability_local_health_host }}")
        self.assertEqual(probe["ansible.builtin.uri"]["follow_redirects"], "none")
        self.assertFalse(probe["ansible.builtin.uri"]["use_proxy"])
        self.assertNotIn("include_role", str(tasks))


if __name__ == "__main__":
    unittest.main()
