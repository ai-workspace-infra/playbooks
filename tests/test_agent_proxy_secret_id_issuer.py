"""Unit tests for the issuer's Vault calls; no production credentials required."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "roles/vhosts/agent_proxy_secret_id_issuer/files/issuer.py"
spec = importlib.util.spec_from_file_location("issuer", FILE)
issuer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(issuer)


class IssuerTest(unittest.TestCase):
    def test_login_then_issue_wrapped_secret_id_for_exact_role(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "token"
            token.write_text("test-jwt")
            env = {"ISSUER_VAULT_KUBERNETES_MOUNT": "kubernetes-example", "ISSUER_VAULT_KUBERNETES_ROLE": "issuer", "ISSUER_VAULT_APPROLE": "reader", "ISSUER_SERVICE_ACCOUNT_TOKEN_FILE": str(token)}
            with patch.dict(os.environ, env, clear=True), patch.object(issuer, "vault_request", side_effect=[{"auth": {"client_token": "test-token"}}, {"data": {"role_id": "test-role"}}, {"wrap_info": {"token": "test-wrap"}}]) as request:
                self.assertEqual(issuer.issue(), {"role_id": "test-role", "wrapping_token": "test-wrap"})
                self.assertEqual(request.call_args_list[1].args[0], "auth/approle/role/reader/role-id")
                self.assertEqual(request.call_args_list[2].args, ("auth/approle/role/reader/secret-id", {}, "test-token", True))

    def test_role_cannot_escape_path(self):
        with patch.dict(os.environ, {"ISSUER_VAULT_KUBERNETES_MOUNT": "kubernetes", "ISSUER_VAULT_KUBERNETES_ROLE": "issuer", "ISSUER_VAULT_APPROLE": "../other"}, clear=True):
            with self.assertRaises(ValueError):
                issuer.issue()


if __name__ == "__main__":
    unittest.main()
