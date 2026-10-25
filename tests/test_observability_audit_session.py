"""No-network tests for privileged setup, bounded lifetime, audit and cleanup."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "roles/observability_audit_session/files"
spec = importlib.util.spec_from_file_location("audit_session", FILES / "session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


def args(**kwargs):
    return argparse.Namespace(**({"base_url": "https://grafana.example.test",
        "vault_path": "kv/prod/services/observability", "password_field": "grafana_password",
        "admin_username": "admin", "service_account_name": "observability-audit-prod",
        "cluster": "cluster-fixture", "environment": "prod", "datasource_uid": "victoriametrics",
        "token_ttl": 3600} | kwargs))


class FakeGrafana:
    def __init__(self, existing=True, role="Viewer", disabled=False, revoke_failure=False,
                 token_timeout=False, ttl=3599):
        self.authorization = "management-authorization-sentinel"
        self.existing, self.role, self.disabled = existing, role, disabled
        self.revoke_failure, self.token_timeout, self.ttl = revoke_failure, token_timeout, ttl
        self.calls, self.tokens = [], []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/api/serviceaccounts/search"):
            found = [{"id": 7, "name": "observability-audit-prod"}] if self.existing else []
            return {"totalCount": len(found), "serviceAccounts": found}
        if method == "POST" and path == "/api/serviceaccounts":
            assert payload == {"name": "observability-audit-prod", "role": "Viewer", "isDisabled": False}
            return {"id": 7}
        if method == "GET" and path == "/api/serviceaccounts/7":
            return {"id": 7, "orgId": 1, "name": "observability-audit-prod", "role": self.role,
                    "isDisabled": self.disabled}
        if method == "POST" and path == "/api/serviceaccounts/7/tokens":
            assert payload["secondsToLive"] == 3600
            self.tokens.append({"id": 9, "name": payload["name"], "secondsUntilExpiration": self.ttl,
                                "expiration": (datetime.datetime.now(datetime.timezone.utc)
                                               + datetime.timedelta(seconds=self.ttl)).isoformat()})
            if self.token_timeout:
                raise session.SessionError("management_transport_or_json_error")
            return {"id": 9, "name": payload["name"], "key": "viewer-token-sentinel"}
        if method == "GET" and path == "/api/serviceaccounts/7/tokens":
            return self.tokens.copy()
        if method == "DELETE" and path == "/api/serviceaccounts/7/tokens/9":
            if self.revoke_failure:
                raise session.SessionError("management_http_503")
            self.tokens = [token for token in self.tokens if token["id"] != 9]
            return {"message": "Deleted"}
        raise AssertionError("Unexpected request")


class AuditSessionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"VAULT_ADDR": "https://vault.example.test"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_fake(self, client, verifier=None, **overrides):
        def checked_verifier(config, token, identity):
            self.assertEqual(token, "viewer-token-sentinel")
            self.assertEqual(identity["role"], "Viewer")
            return {"overall_status": "PASS", "accidental_echo": token}
        return session.run(args(**overrides),
                           credential_reader=lambda path, field: "password-sentinel",
                           client_factory=lambda origin, username, password: client,
                           verifier=verifier or checked_verifier)

    def test_existing_viewer_mints_and_revokes_exact_token_without_deleting_sa(self):
        client = FakeGrafana()
        report = self.run_fake(client)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertFalse(report["management"]["created_service_account"])
        self.assertTrue(report["cleanup"]["token_revoked"])
        self.assertEqual(client.tokens, [])
        self.assertEqual([call[1] for call in client.calls if call[0] == "DELETE"],
                         ["/api/serviceaccounts/7/tokens/9"])
        for secret in ["password-sentinel", "viewer-token-sentinel", "management-authorization-sentinel"]:
            self.assertNotIn(secret, json.dumps(report))

    def test_new_sa_is_viewer_and_retained_by_default(self):
        client = FakeGrafana(existing=False)
        report = self.run_fake(client)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertTrue(report["management"]["created_service_account"])
        self.assertFalse(report["cleanup"]["service_account_deleted"])

    def test_preexisting_tokens_are_never_revoked(self):
        client = FakeGrafana()
        other = {"id": 2, "name": "other-consumer", "expiration": None, "secondsUntilExpiration": 0}
        client.tokens.append(other)
        report = self.run_fake(client)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertEqual(client.tokens, [other])

    def test_nonviewer_or_disabled_collision_fails_without_mint(self):
        for client in [FakeGrafana(role="Admin"), FakeGrafana(disabled=True)]:
            report = self.run_fake(client)
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_audit_exception_still_revokes_and_does_not_leak_exception(self):
        def broken(*_):
            raise RuntimeError("viewer-token-sentinel password-sentinel")
        client = FakeGrafana()
        report = self.run_fake(client, broken)
        self.assertEqual(report["management"]["status"], "PASS")
        self.assertEqual(report["audit"]["overall_status"], "FAIL")
        self.assertEqual(report["cleanup"]["status"], "PASS")
        self.assertEqual(client.tokens, [])
        self.assertNotIn("sentinel", json.dumps(report))

    def test_revoke_failure_blocks_with_finite_ttl_and_action(self):
        report = self.run_fake(FakeGrafana(revoke_failure=True))
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertTrue(report["cleanup"]["manual_action_required"])
        self.assertEqual(report["cleanup"]["token_ttl_requested_seconds"], 3600)
        self.assertIn("token_expiration", report["management"])

    def test_post_timeout_cleans_up_only_random_token_name(self):
        client = FakeGrafana(token_timeout=True)
        report = self.run_fake(client)
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertEqual(report["cleanup"]["status"], "PASS")
        self.assertEqual(client.tokens, [])

    def test_wrong_or_unbounded_ttl_is_rejected_and_revoked(self):
        report = self.run_fake(FakeGrafana(ttl=0))
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertEqual(report["cleanup"]["status"], "PASS")
        for ttl in [0, 1799, 3601]:
            with self.assertRaises(session.SessionError):
                self.run_fake(FakeGrafana(), token_ttl=ttl)

    def test_scope_or_transport_failure_happens_before_credentials(self):
        for overrides in [{"base_url": "http://grafana.example.test"},
                          {"vault_path": "kv/uat/services/observability"},
                          {"service_account_name": "admin"}]:
            with self.assertRaises(session.SessionError):
                self.run_fake(FakeGrafana(), **overrides)

    @patch.object(session.subprocess, "run")
    def test_vault_cli_captures_in_memory_and_does_not_take_secret_arguments(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"data": {"data": {"grafana_password": "password-sentinel"}}})
        self.assertEqual(session.vault_password("kv/prod/services/observability", "grafana_password"), "password-sentinel")
        self.assertEqual(run.call_args.args[0], ["vault", "kv", "get", "-format=json", "kv/prod/services/observability"])
        self.assertTrue(run.call_args.kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
