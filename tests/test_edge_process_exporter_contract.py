import json
from pathlib import Path
import tomllib
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]


class EdgeProcessExporterContractTest(unittest.TestCase):
    def test_process_exporter_role_has_defaults_and_consolidates(self):
        role_dir = ROOT / "roles/vhosts/process_exporter"
        defaults_file = role_dir / "defaults/main.yml"
        self.assertTrue(defaults_file.exists(), "defaults/main.yml must exist")
        defaults = yaml.safe_load(defaults_file.read_text())
        self.assertEqual(defaults.get("group"), "all")
        self.assertEqual(defaults.get("process_exporter_port"), 9256)
        self.assertEqual(defaults.get("process_exporter_version"), "0.7.10")

        tasks = yaml.safe_load((role_dir / "tasks/main.yml").read_text())
        candidate_task = next(
            t for t in tasks if t.get("name") == "Check whether node-exporter is available for combined metrics service"
        )
        self.assertIn("/opt/metrics-agent/node_exporter", candidate_task["loop"])
        self.assertIn("/usr/local/bin/xconnect-node-exporter", candidate_task["loop"])
        self.assertIn("/usr/local/bin/node_exporter", candidate_task["loop"])

        metrics_unit = (role_dir / "templates/metrics.service").read_text()
        self.assertIn("metrics-supervisor.py", metrics_unit)
        self.assertIn("127.0.0.1:9100", metrics_unit)
        self.assertIn("{{ process_exporter_port | default('9256') }}", metrics_unit)

    def test_vpn_overlay_observability_process_metrics(self):
        role_dir = ROOT / "roles/vhosts/vpn-overlay/observability"
        tasks_text = (role_dir / "tasks/main.yml").read_text()
        self.assertIn("xconnect-process-exporter", tasks_text)
        self.assertIn("xconnect-process-exporter.service", tasks_text)
        self.assertIn("xconnect-process-exporter.yml", tasks_text)

        env = Environment(undefined=StrictUndefined)
        values = {
            "xconnect_observability_role": "one",
            "xconnect_observability_environment": "prod",
            "xconnect_observability_instance": "jp-xconnect-01.svc.plus",
            "xconnect_observability_process_metrics_enabled": True,
            "xconnect_observability_process_port": 19256,
            "xconnect_observability_endpoint": "https://observability.svc.plus",
            "xconnect_observability_auth_user": "test-user",
            "xconnect_observability_auth_password": "test-password",
        }
        rendered = env.from_string((role_dir / "templates/vector.toml.j2").read_text()).render(values)
        config = tomllib.loads(rendered)

        self.assertIn("process_metrics", config["sources"])
        self.assertEqual(config["sources"]["process_metrics"]["endpoints"], ["http://127.0.0.1:19256/metrics"])
        self.assertIn("process_metrics", config["transforms"]["add_xconnect_labels"]["inputs"])

        vrl = config["transforms"]["add_xconnect_labels"]["source"]
        self.assertIn('.tags.job = "xconnect"', vrl)
        self.assertIn('starts_with(string!(.name), "node_")', vrl)
        self.assertIn('.tags.job = "node-exporter"', vrl)
        self.assertIn('starts_with(string!(.name), "namedprocess_")', vrl)
        self.assertIn('.tags.job = "process-exporter"', vrl)
        self.assertIn('.tags.instance = "jp-xconnect-01.svc.plus"', vrl)

    def test_regional_and_edge_playbooks_include_process_exporters(self):
        regional_playbook = (ROOT / "deploy_xconnect_regional.yml").read_text()
        self.assertIn("deploy_node_process_exporters.yml", regional_playbook)
        self.assertIn("node_process_exporter_hosts: xconnect", regional_playbook)

        node_process_playbook = (ROOT / "deploy_node_process_exporters.yml").read_text()
        self.assertIn("node_process_exporter_hosts", node_process_playbook)
        self.assertIn("vhosts/node_exporter", node_process_playbook)
        self.assertIn("vhosts/process_exporter", node_process_playbook)
        self.assertIn("vhosts/vector-agent", node_process_playbook)

        prod_v2_playbook = (ROOT / "deploy-prod-v2-edges.yml").read_text()
        self.assertIn("vhosts/node_exporter", prod_v2_playbook)
        self.assertIn("vhosts/process_exporter", prod_v2_playbook)
        self.assertIn("vhosts/vector-agent", prod_v2_playbook)
        self.assertIn("vector_observability_node_identity", prod_v2_playbook)

    def test_cmdb_monitoring_supports_process_exporter(self):
        role_dir = ROOT / "roles/vhosts/cmdb-monitoring"
        env = Environment(undefined=StrictUndefined)
        env.filters["to_json"] = json.dumps
        values = {
            "cmdb_monitoring_instance": "jp-free-selfhost-prod-v2.svc.plus",
            "cmdb_monitoring_provider": "akamai",
            "cmdb_monitoring_project_id": "prod-v2",
            "cmdb_monitoring_logical_project": "prod-v2-free",
            "cmdb_monitoring_environment": "prod",
            "cmdb_monitoring_endpoint": "https://observability.svc.plus/ingest/metrics/api/v1/write",
            "cmdb_monitoring_auth_user": "user",
            "cmdb_monitoring_auth_password": "pass",
            "cmdb_monitoring_process_exporter_enabled": True,
            "cmdb_monitoring_process_port": 19256,
        }
        rendered = env.from_string((role_dir / "templates/vector.toml.j2").read_text()).render(values)
        config = tomllib.loads(rendered)

        self.assertIn("process", config["sources"])
        self.assertEqual(config["sources"]["process"]["endpoints"], ["http://127.0.0.1:19256/metrics"])
        self.assertIn("process", config["transforms"]["labels"]["inputs"])

        vrl = config["transforms"]["labels"]["source"]
        self.assertIn('starts_with(string!(.name), "namedprocess_")', vrl)
        self.assertIn('.tags.job = "process-exporter"', vrl)


if __name__ == "__main__":
    unittest.main()
