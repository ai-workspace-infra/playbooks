import os
import unittest

class TestDataKitHelmRoleContract(unittest.TestCase):
    def setUp(self):
        self.playbooks_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.role_root = os.path.join(self.playbooks_root, "roles", "k8s_helm", "datakit")
        self.defaults_file = os.path.join(self.role_root, "defaults", "main.yml")
        self.template_file = os.path.join(self.role_root, "templates", "values.yaml.j2")

    def test_defaults_file_exists_and_contains_all_scrape_endpoints(self):
        self.assertTrue(os.path.exists(self.defaults_file), f"Defaults file not found: {self.defaults_file}")
        with open(self.defaults_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("datakit_caddy_metrics_url", content)
        self.assertIn("datakit_postgresql_metrics_url", content)
        self.assertIn("datakit_kube_state_metrics_url", content)
        self.assertIn("datakit_node_exporter_url", content)
        self.assertNotIn("host_processes", content, "host_processes should be excluded to prevent trial quota exhaustion")

    def test_template_values_renders_all_four_configs_in_dkconfig(self):
        self.assertTrue(os.path.exists(self.template_file), f"Template file not found: {self.template_file}")
        with open(self.template_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("caddy-metrics.conf", content)
        self.assertIn("postgresql-exporter.conf", content)
        self.assertIn("kube-state-metrics.conf", content)
        self.assertIn("node-exporter.conf", content)
        self.assertIn("measurement_name = \"caddy\"", content)
        self.assertIn("measurement_name = \"postgresql\"", content)
        self.assertIn("measurement_name = \"kube-state-metrics\"", content)
        self.assertIn("measurement_name = \"node-exporter\"", content)

if __name__ == "__main__":
    unittest.main()
