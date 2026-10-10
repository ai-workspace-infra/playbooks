import json
import re
import tomllib
import unittest
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "roles/vhosts/vector-agent/templates/vector.toml.j2"


class VectorEdgeResourcePolicyTests(unittest.TestCase):
    def render(self, docker_running, billing_enabled=False):
        env = Environment()
        env.filters.update(
            bool=lambda value: str(value).lower() in ("true", "1", "yes"),
            to_json=json.dumps,
            regex_replace=lambda value, pattern, replacement: re.sub(pattern, replacement, value),
            urlencode=lambda value: quote(str(value), safe=""),
        )
        source = env.from_string(TEMPLATE.read_text())
        rendered = source.render(
            groups={},
            inventory_hostname="jp-xconnect.svc.plus",
            vector_observability_environment="prod",
            vector_observability_provider="akamai",
            vector_observability_service_domain="svc.plus",
            vector_billing_ingest_enabled=billing_enabled,
            vector_system_journald_enabled=False,
            vector_docker_logs_enabled_effective=docker_running,
            xconnect_node_maintenance={
                "vector": {
                    "internal_metrics_scrape_interval_secs": 30,
                    "log_message_max_bytes": 4096,
                    "sink_batch_max_bytes": 1048576,
                    "sink_batch_timeout_secs": 5,
                    "sink_buffer_max_events": 500,
                    "tls_verify": True,
                }
            },
            blackbox_ssl_targets=[],
            blackbox_exporter_role_defaults={"blackbox_ssl_targets": []},
        )
        return tomllib.loads(rendered)

    def test_memory_and_log_limits_render(self):
        config = self.render(docker_running=False)
        self.assertEqual(config["sources"]["internal_metrics"]["scrape_interval_secs"], 30)
        logs = config["transforms"]["process_logs"]
        self.assertTrue(logs["drop_on_abort"])
        self.assertIn("http.handlers.reverse_proxy", logs["source"])
        self.assertIn("truncate(string!(.message), 4096)", logs["source"])
        self.assertEqual(config["sinks"]["victorialogs"]["batch"]["max_bytes"], 1048576)
        self.assertEqual(config["sinks"]["victorialogs"]["batch"]["timeout_secs"], 5)
        self.assertEqual(config["sinks"]["victorialogs"]["buffer"]["max_events"], 500)
        self.assertTrue(config["sinks"]["victorialogs"]["tls"]["verify_certificate"])

    def test_docker_source_is_removed_when_service_is_inactive(self):
        config = self.render(docker_running=False)
        self.assertNotIn("docker_logs", config["sources"])
        self.assertNotIn("process_docker_logs", config["transforms"])
        self.assertEqual(config["sinks"]["victorialogs"]["inputs"], ["process_logs"])

    def test_docker_source_is_kept_when_service_is_active(self):
        config = self.render(docker_running=True)
        self.assertEqual(config["sources"]["docker_logs"]["type"], "docker_logs")
        self.assertIn("process_docker_logs", config["sinks"]["victorialogs"]["inputs"])

    def test_billing_secret_uses_exec_backend_not_environment_interpolation(self):
        config = self.render(docker_running=False, billing_enabled=True)
        self.assertEqual(config["secret"]["local_secret"]["type"], "exec")
        self.assertEqual(
            config["secret"]["local_secret"]["command"],
            ["/usr/local/libexec/vector-secret-provider"],
        )
        self.assertIn("SECRET[local_secret.billing_token]", TEMPLATE.read_text())
        self.assertNotIn("${BILLING_TOKEN}", TEMPLATE.read_text())


if __name__ == "__main__":
    unittest.main()
