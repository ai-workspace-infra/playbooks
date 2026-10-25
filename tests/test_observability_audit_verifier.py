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
                 user_overrides=None, cadvisor_absent=False, missing_range_node=False,
                 ksm_node_change=None, targets_mode=None, process_partial=False, process_zero=False,
                 duplicate_metric=None):
        self.calls = []
        self.write, self.missing_node, self.source_gap = write, missing_node, source_gap
        self.query_error = query_error
        self.user_overrides = user_overrides or {}
        self.cadvisor_absent = cadvisor_absent
        self.missing_range_node = missing_range_node
        self.ksm_node_change = ksm_node_change
        self.targets_mode, self.process_partial, self.process_zero = targets_mode, process_partial, process_zero
        self.duplicate_metric = duplicate_metric
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
        if path.endswith("/targets"):
            if self.targets_mode == "forbidden":
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
            targets = []
            for job in ["node-exporter", "process-exporter", "kube-state-metrics", "kubelet", "cadvisor"]:
                for node in ([""] if job == "kube-state-metrics" else ["node-a", "node-b"]):
                    targets.append({"labels": {"cluster": "cluster-fixture", "environment": "prod", "job": job, "node": node},
                                    "health": "up", "lastError": "", "lastScrape": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "scrapeUrl": "https://sensitive-sentinel:credential@target/metrics",
                                    "discoveredLabels": {"credential": "sensitive-sentinel"}})
            if self.targets_mode == "duplicate":
                targets.append(targets[0])
            if self.targets_mode == "down":
                targets[0]["health"], targets[0]["lastError"] = "down", "sensitive-sentinel"
            if self.targets_mode == "stale":
                targets[0]["lastScrape"] = "2020-01-01T00:00:00Z"
            if self.targets_mode == "wrong_node":
                targets[0]["labels"]["node"] = "unknown-node"
            return Response({"status": "success", "data": {"activeTargets": [] if self.targets_mode == "empty" else targets}})
        parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        expr = parameters["query"][0]
        if self.query_error:
            return Response({"status": "error", "error": "fixture failure"})
        if self.cadvisor_absent and 'job="cadvisor"' in expr:
            return Response({"status": "success", "data": {"result": []}})
        result = []
        if path.endswith("query_range"):
            end = float(parameters["end"][0])
            stamps = [end - 1800 + i * 60 for i in range(31)]
            if self.source_gap:
                stamps = [stamp for i, stamp in enumerate(stamps) if i not in [10, 11, 12, 13, 14]]
            nodes = ["node-a"] if self.missing_range_node and 'job="cadvisor"' in expr else ["node-a", "node-b"]
            result = [{"metric": {"node": node}, "values": [[stamp, str(stamp)] for stamp in stamps]}
                      for node in nodes]
        elif expr.startswith("sum by(node)(namedprocess_"):
            value = "3" if "num_procs" in expr else "0"
            if self.process_partial and "partial_errors" in expr:
                value = "15"
            if self.process_zero and "num_procs" in expr:
                value = "0"
            result = [{"metric": {"node": node}, "value": [time.time(), value]} for node in ["node-a", "node-b"]]
        elif self.duplicate_metric and self.duplicate_metric in expr and " > 1" in expr:
            result = [{"metric": {"node": "node-a"}, "value": [time.time(), "2"]}]
        elif "__nonexistent_obs_probe__" in expr or "__invalid_env__" in expr or " > 1" in expr or 'job!="cadvisor"' in expr:
            result = []
        elif expr.startswith("kube_node_info{") or (expr.startswith("up{") and " == 1" in expr):
            nodes = ["node-a"] if self.missing_node and 'job="process-exporter"' in expr else ["node-a", "node-b"]
            if expr.startswith("kube_node_info{") and self.ksm_node_change == "missing":
                nodes = ["node-a"]
            if expr.startswith("kube_node_info{") and self.ksm_node_change == "extra":
                nodes = ["node-a", "node-b", "unknown-node"]
            result = [{"metric": {"node": node}, "value": [time.time(), "1"]} for node in nodes]
        elif expr.startswith("time()"):
            result = [{"metric": {"node": node}, "value": [time.time(), "30"]} for node in ["node-a", "node-b"]]
        else:
            value = "0" if "scrape_errors" in expr else "1"
            result = [{"metric": {}, "value": [time.time(), value]}]
        return Response({"status": "success", "data": {"result": result}})


class AuditVerifierTests(unittest.TestCase):
    def run_fixture(self, fixture, expected_nodes=None):
        config = argparse.Namespace(base_url="https://grafana.example.test", cluster="cluster-fixture",
                                    environment="prod", datasource_uid="victoriametrics",
                                    expected_node=["node-a", "node-b"] if expected_nodes is None else expected_nodes)
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
        report = self.run_fixture(Fixture(user_overrides={"uid": "service-account:7"}))
        self.assertEqual(report["checks"]["viewer_identity"]["status"], "PASS")
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

    def test_old_otel_same_named_series_cannot_pass_cadvisor_acceptance(self):
        fixture = Fixture(cadvisor_absent=True)
        report = self.run_fixture(fixture)
        for key in ["cadvisor_cpu", "cadvisor_memory", "continuity_cadvisor"]:
            self.assertEqual(report["checks"][key]["status"], "FAIL")
        self.assertEqual(report["checks"]["continuity_node"]["status"], "PASS")
        expressions = [urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["query"][0]
                       for request in fixture.calls if "/api/v1/query" in request.full_url]
        cadvisor = [expr for expr in expressions if 'job="cadvisor"' in expr and "container_" in expr]
        self.assertTrue(cadvisor)
        for expr in cadvisor:
            self.assertIn('container!=""', expr)
            self.assertIn('container!="POD"', expr)

    def test_range_requires_every_external_expected_node(self):
        report = self.run_fixture(Fixture(missing_range_node=True))
        check = report["checks"]["continuity_cadvisor"]
        self.assertEqual(check["status"], "FAIL")
        self.assertEqual(check["missing_node_count"], 1)
        self.assertEqual(check["covered_seconds"], 0)

    def test_ksm_cannot_self_attest_missing_or_extra_node_inventory(self):
        for change in ["missing", "extra"]:
            report = self.run_fixture(Fixture(ksm_node_change=change))
            self.assertEqual(report["checks"]["node_inventory"]["status"], "FAIL")

    def test_missing_external_node_input_is_not_run(self):
        report = self.run_fixture(Fixture(), expected_nodes=[])
        self.assertEqual(report["checks"]["external_node_inventory"]["status"], "NOT_RUN")
        self.assertNotIn("continuity_cadvisor", report["checks"])

    def test_active_targets_detect_duplicates_down_stale_and_wrong_nodes_without_retaining_content(self):
        for mode in ["duplicate", "down", "stale", "wrong_node"]:
            with self.subTest(mode=mode):
                report = self.run_fixture(Fixture(targets_mode=mode))
                self.assertEqual(report["checks"]["active_targets"]["status"], "FAIL")
                self.assertNotIn("sensitive-sentinel", json.dumps(report))
        self.assertEqual(self.run_fixture(Fixture())["checks"]["active_targets"]["status"], "PASS")

    def test_unavailable_targets_is_not_zero_healthy_targets(self):
        report = self.run_fixture(Fixture(targets_mode="forbidden"))
        self.assertEqual(report["checks"]["active_targets"]["status"], "NOT_RUN")
        report = self.run_fixture(Fixture(targets_mode="empty"))
        self.assertEqual(report["checks"]["active_targets"]["status"], "FAIL")

    def test_cpu_and_rss_duplicate_queries_preserve_cpu_dimension_and_recent_samples(self):
        for metric, key in [("container_cpu_usage_seconds_total", "cpu"), ("container_memory_rss", "rss")]:
            fixture = Fixture(duplicate_metric=metric)
            report = self.run_fixture(fixture)
            self.assertEqual(report["checks"]["duplicate_container_" + key]["status"], "FAIL")
            expressions = [urllib.parse.parse_qs(urllib.parse.urlsplit(r.full_url).query)["query"][0]
                           for r in fixture.calls if "/api/v1/query?" in r.full_url]
            duplicate = next(expr for expr in expressions if metric in expr and " > 1" in expr)
            self.assertIn("<= 45", duplicate)
            self.assertIn('container!="POD"', duplicate)
            self.assertIn("container,cpu)" if key == "cpu" else "pod,container)", duplicate)

    def test_nonroot_partial_visibility_is_explicit_not_run_and_zero_processes_fail(self):
        report = self.run_fixture(Fixture(process_partial=True))
        self.assertEqual(report["checks"]["process_partial_errors"]["status"], "NOT_RUN")
        self.assertEqual(report["checks"]["process_partial_errors"]["total"], 30)
        self.assertEqual(report["checks"]["process_positive_process_count"]["status"], "PASS")
        report = self.run_fixture(Fixture(process_zero=True))
        self.assertEqual(report["checks"]["process_positive_process_count"]["status"], "FAIL")


class AlertDependencyTests(unittest.TestCase):
    def scan(self, ruler, provisioning, permissions=None, version=""):
        def get(path):
            return ruler if "/ruler/" in path else provisioning
        return verifier.scan_alert_dependencies(get, permissions or {
            "alert.rules:read": ["folders:*"], "folders:read": ["folders:*"],
            "datasources:query": ["datasources:*"]}, version)

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

    def test_grafana13_provisioning_global_folder_read_needs_no_global_datasource_query(self):
        permissions = {"alert.rules:read": ["folders:*"], "folders:read": ["folders:*"],
                       "datasources:query": ["datasources:uid:metrics"]}
        report = self.scan((200, {}), (200, []), permissions, "13.2.4")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["provisioning_completeness_basis"], "grafana_13.2.4_all_folder_rule_and_folder_read")
        self.assertEqual(report["read_scope_evidence"]["datasources:query"], ["datasources:uid:metrics"])
        for version in ["", "future-unknown"]:
            self.assertEqual(self.scan((200, {}), (200, []), permissions, version)["status"], "NOT_RUN")
        limited = permissions | {"folders:read": ["folders:uid:one"]}
        self.assertEqual(self.scan((200, {}), (200, []), limited, "13.2.4")["status"], "NOT_RUN")



if __name__ == "__main__":
    unittest.main()
