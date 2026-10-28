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
        self.assertEqual(names[:3], ["DS_METRICS", "environment", "cluster"])
        self.assertEqual(self.dashboard["templating"]["list"][1]["current"], {})
        self.assertEqual(self.dashboard["templating"]["list"][2]["current"], {})
        self.assertEqual(self.dashboard["uid"], "StarsL_cn_K8S")

    def test_cadvisor_metric_owner_and_application_sandbox_scope(self):
        import re
        queries = {(p.get("id"), t.get("refId")): t.get("expr", "")
                   for p in module.panels(self.dashboard["panels"])
                   for t in p.get("targets", [])}
        expected = [(44,"C"),(45,"C"),(71,"C"),(79,"C"),(75,"I"),
                    (76,"I"),(52,"I"),(52,"J"),(52,"M"),(52,"P")]
        for key in expected:
            self.assertIn('container!="POD"', queries[key], key)
        for key, expr in queries.items():
            for selector in re.finditer(r'\bcontainer_[A-Za-z0-9_:]+\{((?:\$\{[^}]+\}|[^{}])*)\}', expr):
                self.assertIn('job="cadvisor"', selector.group(1), key)
        # Node root cgroup CPU is an intentional total, not application usage.
        for key in [(72,"C"),(80,"C")]:
            self.assertIn('id="/"', queries[key])
            self.assertNotIn('container!="POD"', queries[key])
        # Pod network is often exposed by the sandbox, so retain it.
        for expr in queries.values():
            if 'container_network_' in expr:
                for selector in re.finditer(r'container_network_[A-Za-z0-9_]+\{((?:\$\{[^}]+\}|[^{}])*)\}', expr):
                    self.assertNotIn('container!="POD"', selector.group(1))

    def test_conditional_panels_keep_real_queries_and_explain_absence(self):
        panels = {p['id']: p for p in module.panels(self.dashboard['panels'])}
        count = next(t['expr'] for t in panels[88]['targets'] if t['refId'] == 'B')
        self.assertEqual(count, module.untainted_node_count())
        self.assertIn('count by(cluster,node)', count)
        self.assertIn('or on(cluster)', count)
        self.assertNotIn('vector(0)', count)
        for ref in ('A', 'D'):
            detail = next(t['expr'] for t in panels[88]['targets'] if t['refId'] == ref)
            self.assertNotIn(' or ', detail)
        self.assertIn('0.5 CPU', panels[86]['description'])
        self.assertIn('1 GiB', panels[85]['description'])
        self.assertTrue(panels[86]['targets'][0]['expr'].endswith('>0.5'))
        self.assertTrue(panels[85]['targets'][0]['expr'].endswith('> 1*1024*1024*1024'))
        for pid, refs in [(47, ('J', 'S')), (87, ('J', 'M'))]:
            self.assertIn('k3s/containerd', panels[pid]['description'])
            for ref in refs:
                expr = next(t['expr'] for t in panels[pid]['targets'] if t['refId'] == ref)
                self.assertIn('container_fs_', expr)
                self.assertNotIn(' or ', expr)


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
