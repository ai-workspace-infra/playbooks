from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/docker/observability-server"


class ObservabilityMcpMatrixContractTest(unittest.TestCase):
    def test_all_four_mcp_services_have_explicit_enable_guards_and_ports(self):
        compose = (ROLE / "templates/docker-compose.yml.j2").read_text(encoding="utf-8")
        defaults = (ROLE / "defaults/main.yml").read_text(encoding="utf-8")
        prometheus = (ROLE / "templates/prometheus.yml.j2").read_text(encoding="utf-8")
        caddy = (ROLE / "templates/observability.caddy.j2").read_text(encoding="utf-8")
        expected = {
            "grafana": ("xstream_mcp_grafana", 8000),
            "victoriametrics": ("xstream_mcp_victoriametrics", 8088),
            "victorialogs": ("xstream_mcp_victorialogs", 8083),
            "victoriatraces": ("xstream_mcp_victoriatraces", 8082),
        }
        for component, (container, port) in expected.items():
            with self.subTest(component=component):
                self.assertIn("observability_mcp_enabled | default(false) | bool", compose)
                self.assertIn(f"observability_mcp_{component}_enabled | default(false) | bool", compose)
                self.assertIn(f"container_name: {container}", compose)
                self.assertIn(f"observability_mcp_{component}_enabled: false", defaults)
                self.assertIn(f"observability_mcp_{component}_port: {port}", defaults)
                self.assertIn(f"observability_mcp_{component}_port | default({port})", compose)
                self.assertIn(f"observability_mcp_{component}_port | default({port})", prometheus)
                self.assertIn(f"observability_mcp_{component}_port | default({port})", caddy)

    def test_tasks_scrape_targets_and_ingress_share_the_same_toggles(self):
        tasks = (ROLE / "tasks/mcp.yml").read_text(encoding="utf-8")
        prometheus = (ROLE / "templates/prometheus.yml.j2").read_text(encoding="utf-8")
        caddy = (ROLE / "templates/observability.caddy.j2").read_text(encoding="utf-8")
        self.assertIn("observability_mcp_enabled: false", (ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
        for component in ("grafana", "victoriametrics", "victorialogs", "victoriatraces"):
            with self.subTest(component=component):
                toggle = f"observability_mcp_{component}_enabled | default(false) | bool"
                self.assertIn("observability_mcp_enabled | default(false) | bool", prometheus)
                self.assertIn("observability_mcp_enabled | default(false) | bool", caddy)
                self.assertIn(toggle, tasks)
                self.assertIn(toggle, prometheus)
                self.assertIn(toggle, caddy)

    def test_each_mcp_depends_on_and_targets_its_matching_core_service(self):
        compose = (ROLE / "templates/docker-compose.yml.j2").read_text(encoding="utf-8")
        env_templates = {
            "grafana": ("grafana", "GRAFANA_URL={{ observability_mcp_grafana_url }}"),
            "victoriametrics": ("victoria-metrics", "VM_INSTANCE_ENTRYPOINT={{ observability_mcp_victoriametrics_entrypoint }}"),
            "victorialogs": ("victoria-logs", "VL_INSTANCE_ENTRYPOINT={{ observability_mcp_victorialogs_entrypoint }}"),
            "victoriatraces": ("victoria-traces", "VT_INSTANCE_ENTRYPOINT={{ observability_mcp_victoriatraces_entrypoint }}"),
        }
        for component, (backend, env_line) in env_templates.items():
            with self.subTest(component=component):
                service = compose.split(f"  mcp-{component}:\n", 1)[1].split("{% endif %}", 1)[0]
                self.assertIn(f"- {backend}", service)
                template = (ROLE / f"templates/mcp-{component}.env.j2").read_text(encoding="utf-8")
                self.assertIn(env_line, template)

    def test_logs_port_avoids_xray_exporter_port(self):
        defaults = (ROLE / "defaults/main.yml").read_text(encoding="utf-8")
        compose = (ROLE / "templates/docker-compose.yml.j2").read_text(encoding="utf-8")
        readme = (ROLE / "README.md").read_text(encoding="utf-8")
        self.assertIn("observability_mcp_victorialogs_port: 8083", defaults)
        self.assertIn("`127.0.0.1:8083`", readme)


if __name__ == "__main__":
    unittest.main()
