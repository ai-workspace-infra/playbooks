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
    def __init__(self, write=False, missing_node=False, source_gap=False, query_error=False,
                 user_overrides=None):
        self.calls = []
        self.write, self.missing_node, self.source_gap = write, missing_node, source_gap
        self.query_error = query_error
        self.user_overrides = user_overrides or {}
    def open(self, request, timeout):
        self.calls.append(request)
        assert request.get_method() == "GET"
        path = urllib.parse.urlsplit(request.full_url).path
        if path == "/api/health":
            return Response({"database": "ok", "version": "fixture"})
        if path == "/api/datasources":
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        if path == "/api/user":
            return Response({"id": 7, "orgId": 1, "isGrafanaAdmin": False,
                             "login": "sa-1-observability-audit-prod", "name": "observability-audit-prod",
                             "uid": "fixture-sa-uid"} | self.user_overrides)
        if path == "/api/access-control/user/permissions":
            return Response({"dashboards:write" if self.write else "dashboards:read": ["*"],
                             "alert.rules:read": ["folders:*"], "folders:read": ["folders:*"],
                             "datasources:query": ["datasources:*"]})
        if path == "/api/ruler/grafana/api/v1/rules":
            return Response({})
        if path == "/api/v1/provisioning/alert-rules":
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
        if path == "/api/search":
            return Response([{"uid": uid, "type": "dash-db"} for uid in verifier.EXPECTED_UIDS])
        if path.endswith("/StarsL_cn_K8S"):
            return Response({"dashboard": {"templating": {"list": [{"name": name} for name in ["DS_METRICS", "cluster", "environment"]]}}})
        if path.endswith("/web-saas-k3s-v5-overview"):
            return Response({"dashboard": {"panels": [{"targets": [{"expr": name + '{cluster="$cluster",environment="$environment"}'}]}
                                                      for name in ["k8s_node_cpu_utilization_ratio", "k8s_node_memory_working_set_bytes", "container_memory_working_set_bytes"]]}})
        parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        expr = parameters["query"][0]
        if self.query_error:
            return Response({"status": "error", "error": "fixture failure"})
        result = []
        if path.endswith("query_range"):
            end = float(parameters["end"][0])
            stamps = [end - 1800 + i * 60 for i in range(31)]
            if self.source_gap:
                stamps = [stamp for i, stamp in enumerate(stamps) if i not in [10, 11, 12, 13, 14]]
            result = [{"metric": {"node": node}, "values": [[stamp, str(stamp)] for stamp in stamps]}
                      for node in ["node-a", "node-b"]]
        elif "__nonexistent_obs_probe__" in expr or "__invalid_env__" in expr or " > 1" in expr or 'job!="cadvisor"' in expr:
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
            return verifier.verify(config, "viewer-token-sentinel", {"id": 7, "orgId": 1, "role": "Viewer",
                "login": "sa-1-observability-audit-prod", "name": "observability-audit-prod"})

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

    def test_grafana13_service_account_uid_dto_is_exactly_bound(self):
        report = self.run_fixture(Fixture(user_overrides={"id": 0, "uid": "service-account:7", "role": None}))
        identity = report["checks"]["viewer_identity"]
        self.assertEqual(identity["status"], "PASS")
        self.assertTrue(identity["service_account_uid_matches"])
        self.assertFalse(identity["numeric_id_matches"])

    def test_wrong_uid_login_name_org_or_admin_flag_is_rejected(self):
        baseline = {"id": 0, "uid": "service-account:7"}
        for mismatch in [{"uid": "service-account:8"}, {"login": "sa-other"},
                         {"name": "other"}, {"orgId": 2}, {"isGrafanaAdmin": True}]:
            with self.subTest(mismatch=mismatch):
                report = self.run_fixture(Fixture(user_overrides=baseline | mismatch))
                self.assertEqual(report["checks"]["viewer_identity"]["status"], "FAIL")
                self.assertNotIn("permission_introspection", report["checks"])

    def test_legacy_id_still_requires_login_binding_and_no_conflicting_sa_uid(self):
        for mismatch in [{"login": "sa-other"}, {"uid": "service-account:8"}]:
            report = self.run_fixture(Fixture(user_overrides=mismatch))
            self.assertEqual(report["checks"]["viewer_identity"]["status"], "FAIL")

    def test_all_node_coverage_detects_missing_node(self):
        report = self.run_fixture(Fixture(missing_node=True))
        self.assertEqual(report["checks"]["nodes_process-exporter"]["status"], "FAIL")

    def test_source_timestamp_gap_cannot_hide_behind_query_lookback(self):
        report = self.run_fixture(Fixture(source_gap=True))
        self.assertEqual(report["checks"]["continuity_cadvisor"]["status"], "FAIL")
        self.assertGreater(report["checks"]["continuity_cadvisor"]["max_source_sample_gap_seconds"], 180)

    def test_http_200_query_errors_cannot_pass_empty_result_negative_checks(self):
        report = self.run_fixture(Fixture(query_error=True))
        for key in ["nonexistent_cluster", "nonexistent_environment", "duplicate_container_memory"]:
            self.assertEqual(report["checks"][key]["status"], "FAIL")


class AlertDependencyTests(unittest.TestCase):
    def scan(self, ruler, provisioning, permissions=None):
        def get(path):
            return ruler if "/ruler/" in path else provisioning
        return verifier.scan_alert_dependencies(get, permissions or {
            "alert.rules:read": ["folders:*"], "folders:read": ["folders:*"],
            "datasources:query": ["datasources:*"]})

    def test_empty_ruler_requires_complete_scope_and_does_not_use_admin(self):
        report = self.scan((200, {}), (403, {}))
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["scope_completeness_verified"])
        report = self.scan((200, {}), (403, {}), {"alert.rules:read": ["folders:uid:one"]})
        self.assertEqual(report["status"], "NOT_RUN")

    def test_forbidden_or_malformed_rule_reads_are_not_zero_rules(self):
        for ruler in [(403, {}), (404, {}), (200, {"message": "not rules"}),
                      (200, {"folder": [{"rules": [{"grafana_alert": {"uid": "a", "data": []}}]}]})]:
            with self.subTest(ruler=ruler):
                self.assertEqual(self.scan(ruler, (403, {}))["status"], "NOT_RUN")

    def test_otel_label_dependency_is_reported_only_as_hash_and_matches(self):
        expression = 'sum by(k8s_pod_name)(container_memory_working_set_bytes{job="otel-collector",note="sensitive-literal"})'
        ruler = {"folder": [{"rules": [{"grafana_alert": {"uid": "rule-a", "data": [{"model": {"expr": expression}}]}}]}]}
        report = self.scan((200, ruler), (403, {}))
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["rules"][0]["dependency_candidate"])
        self.assertNotIn("sensitive-literal", json.dumps(report))
        self.assertNotIn(expression, json.dumps(report))
        self.assertEqual(len(report["rules"][0]["expr_sha256"][0]), 64)

    def test_shared_metric_remains_review_pending_not_classified_unique(self):
        rule = {"uid": "rule-a", "data": [{"model": {"expr": "sum(container_memory_usage_bytes)"}}]}
        report = self.scan((403, {}), (200, [rule]), {"alert.rules.provisioning:read": [""]})
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertTrue(report["rules"][0]["shared_metric_review"])
        self.assertFalse(report["rules"][0]["dependency_candidate"])



if __name__ == "__main__":
    unittest.main()
