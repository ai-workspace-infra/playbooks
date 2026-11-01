"""Tests for the SecretID transport; no real credentials or network are used."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "roles/vhosts/agent_proxy_vector_vault_auth/files/fetch-agent-proxy-approle"
loader = importlib.machinery.SourceFileLoader("identity_fetch", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

ENV = {
    "VAULT_ADDR": "https://vault.example",
    "AGENT_PROXY_ISSUER_URL": "https://issuer.example/v1/secret-id",
    "AGENT_PROXY_ISSUER_CA_FILE": "/etc/test-ca",
    "AGENT_PROXY_CLIENT_CERT_FILE": "/etc/test-cert",
    "AGENT_PROXY_CLIENT_KEY_FILE": "/run/test-key",
    "AGENT_PROXY_SECRET_ID_CREATION_PATH": "auth/approle/role/reader/secret-id",
    "AGENT_PROXY_ROLE_ID_FILE": "/run/test/role-id",
    "AGENT_PROXY_SECRET_ID_FILE": "/run/test/secret-id",
}


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, *args):
        return json.dumps(self.value).encode()


class IdentityFetchTest(unittest.TestCase):
    def test_accepts_only_matching_wrapping_path(self):
        for creation_path, expected in ((ENV["AGENT_PROXY_SECRET_ID_CREATION_PATH"], 0),
                                        ("auth/approle/role/other/secret-id", 1)):
            with self.subTest(creation_path=creation_path):
                def request(req, timeout):
                    value = {"creation_path": creation_path} if req.full_url.endswith("/lookup") else {"secret_id": "test-secret"}
                    return Response({"data": value})
                with patch.dict(os.environ, ENV, clear=True), \
                     patch.object(module.ssl, "create_default_context") as context, \
                     patch.object(module.urllib.request, "build_opener", return_value=types.SimpleNamespace(open=lambda *args, **kwargs: Response({"role_id": "test-role", "wrapping_token": "test-wrap"}))), \
                     patch.object(module.urllib.request, "urlopen", side_effect=request), \
                     patch.object(module, "install") as install:
                    self.assertEqual(module.main(), expected)
                    self.assertEqual(install.call_count, 2 if expected == 0 else 0)
                    context.assert_called_once_with(cafile=ENV["AGENT_PROXY_ISSUER_CA_FILE"])
                    context.return_value.load_cert_chain.assert_called_once_with(ENV["AGENT_PROXY_CLIENT_CERT_FILE"], ENV["AGENT_PROXY_CLIENT_KEY_FILE"])

    def test_issuer_failure_does_not_install(self):
        with patch.dict(os.environ, ENV, clear=True), \
             patch.object(module.ssl, "create_default_context", side_effect=OSError("unavailable")), \
             patch.object(module, "install") as install:
            self.assertEqual(module.main(), 1)
            install.assert_not_called()

    def test_identity_path_cannot_escape_run(self):
        with self.assertRaises(ValueError):
            module.install("/run/../etc/unsafe", "test")


if __name__ == "__main__":
    unittest.main()
