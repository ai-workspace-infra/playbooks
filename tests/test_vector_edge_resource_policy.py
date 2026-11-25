import json
import re
import tomllib
import unittest
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "roles/vhosts/vector-agent/templates/vector.toml.j2"


class VectorEdgeResourcePolicyTests(unittest.TestCase):
    def render(self, docker_running, billing_enabled=False, vector_policy=None, xhttp=False):
        env = Environment(loader=FileSystemLoader(TEMPLATE.parent))
        env.filters.update(
            bool=lambda value: str(value).lower() in ("true", "1", "yes"),
            to_json=json.dumps,
            regex_replace=lambda value, pattern, replacement: re.sub(pattern, replacement, value),
            urlencode=lambda value: quote(str(value), safe=""),
        )
        policy = {
            "internal_metrics_scrape_interval_secs": 30,
            "log_message_max_bytes": 4096,
            "sink_batch_max_bytes": 1048576,
            "sink_batch_timeout_secs": 5,
            "sink_buffer_max_events": 500,
            "tls_verify": True,
        }
        if vector_policy:
            policy.update(vector_policy)
        source = env.from_string(TEMPLATE.read_text())
        rendered = source.render(
            groups={},
            inventory_hostname="jp-xconnect.svc.plus",
            vector_observability_node_identity="jp-xconnect.svc.plus",
            vector_observability_environment="prod",
            vector_observability_provider="akamai",
            vector_observability_service_domain="svc.plus",
            vector_billing_ingest_enabled=billing_enabled,
            vector_xray_exporter_enabled=billing_enabled,
            vector_system_journald_enabled=False,
            vector_xhttp_monitoring_enabled=xhttp,
            vector_docker_logs_enabled_effective=docker_running,
            xconnect_node_maintenance={"vector": policy},
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

    def test_edge_memory_policy_can_disable_system_logs_and_tighten_buffers(self):
        policy = {
            "system_logs_enabled": False,
            "billing_disk_buffer_max_bytes": 268435488,
            "prometheus_batch_max_events": 250,
            "prometheus_buffer_max_events": 500,
            "sink_batch_max_bytes": 262144,
            "sink_buffer_max_events": 250,
        }
        config = self.render(docker_running=False, billing_enabled=True, vector_policy=policy, xhttp=True)
        self.assertNotIn("system_logs", config["sources"])
        self.assertEqual(config["transforms"]["process_logs"]["inputs"], ["xhttp_journal"])
        self.assertEqual(config["sinks"]["billing_snapshot_ingest"]["buffer"]["max_size"], 268435488)
        self.assertEqual(config["sinks"]["prometheus_remote"]["batch"]["max_events"], 250)
        self.assertEqual(config["sinks"]["prometheus_remote"]["buffer"]["max_events"], 500)
        self.assertEqual(config["sinks"]["victorialogs"]["batch"]["max_bytes"], 262144)

    def test_system_logs_remains_enabled_when_no_journal_source_is_active(self):
        config = self.render(docker_running=False, vector_policy={"system_logs_enabled": False}, xhttp=False)
        self.assertIn("system_logs", config["sources"])
        self.assertEqual(config["transforms"]["process_logs"]["inputs"], ["system_logs"])

    def test_systemd_override_renders_jemalloc_and_tight_memory_bounds(self):
        env = Environment(loader=FileSystemLoader(TEMPLATE.parent))
        env.filters["bool"] = lambda value: str(value).lower() in ("true", "1", "yes")
        override = env.get_template("vector.service.d_override.conf.j2").render(
            groups={},
            xconnect_node_maintenance={
                "vector": {
                    "threads": 1,
                    "tokio_worker_threads": 1,
                    "malloc_arena_max": 2,
                    "malloc_conf": "narenas:1,dirty_decay_ms:1000,muzzy_decay_ms:0,thp:never,retain:false",
                    "memory_high": "72M",
                    "memory_max": "128M",
                }
            },
        )
        self.assertIn("Environment=TOKIO_WORKER_THREADS=1", override)
        self.assertIn("Environment=MALLOC_ARENA_MAX=2", override)
        self.assertIn("Environment=MALLOC_CONF=narenas:1,dirty_decay_ms:1000,muzzy_decay_ms:0,thp:never,retain:false", override)
        self.assertIn("MemoryHigh=72M", override)
        self.assertIn("MemoryMax=128M", override)


if __name__ == "__main__":
    unittest.main()
