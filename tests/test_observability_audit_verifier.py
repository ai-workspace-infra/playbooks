"""Exercise actual GET-only verification without credentials or remote calls."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_verify", ROOT / "roles/observability_audit_session/files/verify_readonly.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class Response:
    status = 200
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, _):
        return json.dumps(self.value).encode()


class Fixture:
    def __init__(self, write=False, missing_node=False, source_gap=False):
        self.calls = []
        self.write, self.missing_node, self.source_gap = write, missing_node, source_gap
    def open(self, request, timeout):
        self.calls.append(request)
        assert request.get_method() == "GET"
        path = urllib.parse.urlsplit(request.full_url).path
        if path == "/api/health":
            return Response({"database": "ok", "version": "fixture"})
        if path == "/api/datasources":
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        if path == "/api/user":
            return Response({"id": 7, "orgId": 1, "isGrafanaAdmin": False})
        if path == "/api/access-control/user/permissions":
            return Response({"dashboards:write" if self.write else "dashboards:read": ["*"]})
        if path == "/api/search":
            return Response([{"uid": uid, "type": "dash-db"} for uid in verifier.EXPECTED_UIDS])
        if path.endswith("/StarsL_cn_K8S"):
            return Response({"dashboard": {"templating": {"list": [{"name": name} for name in ["DS_METRICS", "cluster", "environment"]]}}})
        if path.endswith("/web-saas-k3s-v5-overview"):
            return Response({"dashboard": {"panels": [{"targets": [{"expr": name + '{cluster="$cluster",environment="$environment"}'}]}
                                                      for name in ["k8s_node_cpu_utilization_ratio", "k8s_node_memory_working_set_bytes", "container_memory_working_set_bytes"]]}})
        parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        expr = parameters["query"][0]
        result = []
        if path.endswith("query_range"):
            end = float(parameters["end"][0])
            stamps = [end - 1800 + i * 60 for i in range(31)]
            if self.source_gap:
                stamps = [stamp for i, stamp in enumerate(stamps) if i not in [10, 11, 12, 13, 14]]
            result = [{"metric": {"node": node}, "values": [[stamp, str(stamp)] for stamp in stamps]}
                      for node in ["node-a", "node-b"]]
        elif "__nonexistent_obs_probe__" in expr or " > 1" in expr or 'job!="cadvisor"' in expr:
            result = []
        elif expr.startswith("kube_node_info{") or (expr.startswith("up{") and " == 1" in expr):
            nodes = ["node-a"] if self.missing_node and 'job="process-exporter"' in expr else ["node-a", "node-b"]
            result = [{"metric": {"node": node}, "value": [time.time(), "1"]} for node in nodes]
        elif expr.startswith("time()"):
            result = [{"metric": {"node": node}, "value": [time.time(), "30"]} for node in ["node-a", "node-b"]]
        else:
            value = "0" if "scrape_errors" in expr else "1"
            result = [{"metric": {}, "value": [time.time(), value]}]
        return Response({"status": "success", "data": {"result": result}})


class AuditVerifierTests(unittest.TestCase):
    def run_fixture(self, fixture):
        config = argparse.Namespace(base_url="https://grafana.example.test", cluster="cluster-fixture",
                                    environment="prod", datasource_uid="victoriametrics")
        with patch.object(verifier.urllib.request, "build_opener", return_value=fixture):
            return verifier.verify(config, "viewer-token-sentinel", {"id": 7, "orgId": 1, "role": "Viewer"})

    def test_real_get_only_paths_and_required_checks(self):
        fixture = Fixture()
        report = self.run_fixture(fixture)
        self.assertEqual(report["checks"]["dashboard_count"]["status"], "PASS")
        self.assertEqual(report["checks"]["nodes_process-exporter"]["status"], "PASS")
        self.assertEqual(report["checks"]["original_k3s_overview"]["status"], "PASS")
        self.assertEqual(report["checks"]["continuity_cadvisor"]["status"], "PASS")
        self.assertEqual(report["checks"]["pvc_statistics"]["status"], "NOT_RUN")
        self.assertEqual(report["overall_status"], "NOT_RUN")
        self.assertNotIn("viewer-token-sentinel", json.dumps(report))
        self.assertTrue(all(request.get_method() == "GET" for request in fixture.calls))

    def test_write_permission_blocks_queries(self):
        fixture = Fixture(write=True)
        report = self.run_fixture(fixture)
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertFalse(any("query?" in request.full_url for request in fixture.calls))

    def test_all_node_coverage_detects_missing_node(self):
        report = self.run_fixture(Fixture(missing_node=True))
        self.assertEqual(report["checks"]["nodes_process-exporter"]["status"], "FAIL")

    def test_source_timestamp_gap_cannot_hide_behind_query_lookback(self):
        report = self.run_fixture(Fixture(source_gap=True))
        self.assertEqual(report["checks"]["continuity_cadvisor"]["status"], "FAIL")
        self.assertGreater(report["checks"]["continuity_cadvisor"]["max_source_sample_gap_seconds"], 180)


if __name__ == "__main__":
    unittest.main()
