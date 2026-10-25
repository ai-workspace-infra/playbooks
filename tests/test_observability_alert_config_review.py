"""Management configuration reads cannot become data queries or Viewer claims."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configuration_review", ROOT / "roles/observability_alert_config_review/files/review_config.py")
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)


def args(**overrides):
    return argparse.Namespace(**({"base_url": "https://grafana.example.test",
        "vault_path": "kv/prod/services/observability", "password_field": "grafana_password",
        "admin_username": "admin", "expected_org_id": 1,
        "management_evidence_ref": "controlled/management-authorization.json", "environment": "prod"} | overrides))


class FakeConfig:
    def __init__(self, rules=None, org=1, error=None, identity_overrides=None):
        self.authorization = "management-authorization-sentinel"
        self.rules = [] if rules is None else rules
        self.org, self.error, self.calls = org, error, []
        self.identity_overrides = identity_overrides or {}
    def request(self, method, path):
        self.calls.append((method, path))
        if self.error:
            raise self.error
        if path == "/api/user":
            return {"login": "admin", "orgId": 1, "isGrafanaAdmin": True,
                    "email": "must-not-be-retained"} | self.identity_overrides
        if path == "/api/org":
            return {"id": self.org, "name": "must-not-be-retained", "address": "private-value"}
        if path == "/api/v1/provisioning/alert-rules":
            return self.rules
        raise AssertionError("Unexpected route")


class ManagementConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"VAULT_ADDR": "https://vault.example.test"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
    def run_owner(self, client, **overrides):
        return owner.review(args(**overrides), credential_reader=lambda *_: "password-sentinel",
                            client_factory=lambda *_: client)

    def test_zero_rules_are_management_config_evidence_not_viewer_attestation(self):
        client = FakeConfig()
        report = self.run_owner(client)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertEqual(report["rule_compatibility"]["rule_count"], 0)
        self.assertFalse(report["is_viewer_scope_attestation"])
        self.assertFalse(report["is_business_data_acceptance"])
        self.assertFalse(report["mutation_performed"])
        self.assertEqual(client.calls, [("GET", "/api/user"), ("GET", "/api/org"), ("GET", "/api/v1/provisioning/alert-rules")])
        for secret in ["password-sentinel", "management-authorization-sentinel", "private-value", "must-not-be-retained"]:
            self.assertNotIn(secret, json.dumps(report))

    def test_org_mismatch_blocks_before_any_rule_read(self):
        client = FakeConfig(org=2)
        report = self.run_owner(client)
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertEqual(client.calls, [("GET", "/api/user"), ("GET", "/api/org")])

    def test_wrong_management_login_org_or_server_admin_rejects_before_config_reads(self):
        for overrides in [{"login": "other"}, {"orgId": 2}, {"orgId": True}, {"isGrafanaAdmin": False},
                          {"isGrafanaAdmin": "true"}]:
            with self.subTest(overrides=overrides):
                client = FakeConfig(identity_overrides=overrides)
                report = self.run_owner(client)
                self.assertEqual(report["overall_status"], "FAIL")
                self.assertEqual(client.calls, [("GET", "/api/user")])

    def test_otel_unique_or_shared_metric_with_old_labels_blocks(self):
        for expr in ["sum(container_cpu_time_seconds_total)",
                     'sum by(k8s_pod_name)(container_memory_working_set_bytes{job="otel-collector"})',
                     'sum({__name__=~"container.*"})']:
            rule = {"uid": "rule-a", "data": [{"model": {"expr": expr}}],
                    "annotations": {"private": "password-sentinel"}}
            report = self.run_owner(FakeConfig([rule]))
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertNotIn(expr, json.dumps(report))
            self.assertNotIn("password-sentinel", json.dumps(report))
            self.assertEqual(len(report["rule_compatibility"]["rules"][0]["expr_sha256"][0]), 64)

    def test_shared_metric_or_unresolved_expression_remains_not_run(self):
        for rule in [{"uid": "rule-a", "data": [{"model": {"expr": "sum(container_memory_usage_bytes)"}}]},
                     {"uid": "rule-a", "data": [{"model": {"rawSql": "sensitive-unparsed-sql"}}]}]:
            report = self.run_owner(FakeConfig([rule]))
            self.assertEqual(report["overall_status"], "NOT_RUN")
            self.assertNotIn("sensitive-unparsed-sql", json.dumps(report))

    def test_unrelated_rule_is_compatible_without_raw_model_persistence(self):
        expr = 'sum(k8s_node_memory_working_set_bytes{cluster="fixture"})'
        report = self.run_owner(FakeConfig([{"uid": "rule-node", "data": [{"model": {"expr": expr}}]}]))
        self.assertEqual(report["overall_status"], "PASS")
        self.assertEqual(report["rule_compatibility"]["rule_count"], 1)
        self.assertNotIn(expr, json.dumps(report))

    def test_malformed_or_forbidden_response_never_becomes_zero_rule_pass(self):
        for client in [FakeConfig({"message": "private-server-error"}),
                       FakeConfig(error=owner.runtime.SessionError("configuration_http_403")),
                       FakeConfig(error=RuntimeError("password-sentinel"))]:
            report = self.run_owner(client)
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertNotIn("private-server-error", json.dumps(report))
            self.assertNotIn("password-sentinel", json.dumps(report))

    def test_transport_disallows_everything_except_three_exact_gets(self):
        client = owner.ConfigClient("https://grafana.example.test", "admin", "password-sentinel")
        for method, path in [("POST", "/api/org"), ("DELETE", "/api/v1/provisioning/alert-rules"),
                             ("GET", "/api/datasources"), ("GET", "/api/datasources/proxy/uid/metrics/api/v1/query"),
                             ("GET", "/api/v1/provisioning/alert-rules/export"), ("GET", "/api/org?orgId=2")]:
            with self.subTest(method=method, path=path):
                with self.assertRaises(owner.runtime.SessionError):
                    client.request(method, path)

    def test_verified_management_reference_environment_and_org_are_required(self):
        for overrides in [{"management_evidence_ref": ""}, {"expected_org_id": 0},
                          {"base_url": "http://grafana.example.test"},
                          {"vault_path": "kv/uat/services/observability"}]:
            with self.assertRaises(owner.runtime.SessionError):
                self.run_owner(FakeConfig(), **overrides)


if __name__ == "__main__":
    unittest.main()
