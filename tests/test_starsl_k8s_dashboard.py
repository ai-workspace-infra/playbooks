"""Pinned source, scope isolation, and non-destructive provisioning contracts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/platform/grafana_starsl_dashboard"
spec = importlib.util.spec_from_file_location("starsl_dashboard", ROLE / "files/render_dashboard.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StarslDashboardTest(unittest.TestCase):
    def setUp(self):
        self.dashboard = module.build(ROLE / "files/upstream.json")

    def test_source_integrity_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tampered.json"
            source.write_bytes((ROLE / "files/upstream.json").read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "checksum"):
                module.build(source)

    def test_every_metric_selector_has_cluster_and_environment(self):
        for panel in module.panels(self.dashboard["panels"]):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                # Metric-selector braces do not start with a dollar, unlike
                # nested Grafana ${...} formatter braces.
                import re
                for match in re.finditer(r"(?<!\$)\{((?:\$\{[^}]+\}|[^{}])*)\}", expr):
                    self.assertIn('cluster=~"${cluster:regex}"', match.group(1), expr)
                    self.assertIn('environment=~"${environment:regex}"', match.group(1), expr)
                self.assertNotIn("origin_prometheus", expr)
                self.assertNotIn("on(pod)", expr)

    def test_security_and_containerd_compatibility(self):
        rendered = json.dumps(self.dashboard)
        for forbidden in ["kube_secret_info", "kube_configmap_info", "cass_jvm_",
                          "container_spec_cpu_quota", "container_spec_memory_limit_bytes",
                          'name=~\\"^k8s_.*', 'image!=\\"\\"']:
            self.assertNotIn(forbidden, rendered)
        for panel in module.panels(self.dashboard["panels"]):
            defaults = panel.get("fieldConfig", {}).get("defaults", {})
            self.assertNotEqual(defaults.get("noValue"), "0")
        names = [v["name"] for v in self.dashboard["templating"]["list"]]
        self.assertEqual(names[:3], ["DS_METRICS", "cluster", "environment"])
        self.assertEqual(self.dashboard["uid"], "StarsL_cn_K8S")

    def test_legacy_output_matches_renderer(self):
        path = ROOT / "roles/docker/observability-server/files/starsl-k8s-dashboard.json"
        self.assertEqual(json.loads(path.read_text()), self.dashboard)

    def test_gitops_preserves_existing_provider_and_replays_identically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").touch()
            target = root / "services/platform-observability/prod/imported-grafana"
            target.mkdir(parents=True)
            (target / "kustomization.yaml").write_text("resources:\n- existing-dashboard.yaml\n")
            values = {"grafana": {"dashboardProviders": [
                {"name": "import-general", "path": "general", "configMaps": ["existing-dashboard"]},
                {"name": "other", "configMaps": ["other-dashboard"]}], "bundleChecksum": "old"}}
            path = target / "source-grafana-values.yaml"
            path.write_text(yaml.safe_dump({"data": {"values.yaml": yaml.safe_dump(values)}}))
            first = module.rendered_files(self.dashboard, gitops_root=root)
            for p, body in first.items():
                p.write_text(body)
            second = module.rendered_files(self.dashboard, gitops_root=root)
            self.assertEqual(first, second)
            actual = yaml.safe_load(yaml.safe_load(path.read_text())["data"]["values.yaml"])
            self.assertEqual(actual["grafana"]["dashboardProviders"][0]["configMaps"],
                             ["existing-dashboard", "starsl-k8s-dashboard"])
            self.assertEqual(actual["grafana"]["dashboardProviders"][1],
                             {"name": "other", "configMaps": ["other-dashboard"]})
            # Replaying an independent source migration must still reload
            # datasource/app provisioning when one of its ConfigMaps changes.
            before = actual["grafana"]["bundleChecksum"]
            (target / "existing-datasource.yaml").write_text(yaml.safe_dump({
                "kind": "ConfigMap", "metadata": {"name": "existing-datasource"},
                "data": {"datasources.yaml": "changed provisioning input"}}))
            refreshed = module.rendered_files(self.dashboard, gitops_root=root)
            after = yaml.safe_load(yaml.safe_load(refreshed[path.resolve()])["data"]["values.yaml"])
            self.assertNotEqual(before, after["grafana"]["bundleChecksum"])


if __name__ == "__main__":
    unittest.main()
