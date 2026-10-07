from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "xconnect-network-bootstrap.py"
SPEC = importlib.util.spec_from_file_location("xconnect_network_bootstrap", SCRIPT)
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)


class NetworkBootstrapOwnerTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[dict[str, str], Path]:
        request = root / "request.json"
        request.write_text(json.dumps({
            "owner_email": "ops@example.test",
            "bootstrap": {
                "controller_url": "https://accounts.svc.plus",
                "network": {"id": "net_shared", "gateway_id": "gw-shared-0"},
                "invite": {"device_id": "gateway-shared", "platform": "linux", "role": "gateway",
                           "expires_at": "__GENERATE_AT_RUNTIME__", "ttl_minutes": 15},
            },
        }))
        request.chmod(0o600)
        response = root / "handoff.json"
        return ({
            "RUNNER_TEMP": str(root),
            "NETWORK_REQUEST_FILE": str(request),
            "NETWORK_RESPONSE_FILE": str(response),
            "NETWORK_ID": "net_shared",
            "ACCOUNTS_API_URL": "https://accounts.svc.plus",
            "INVITATION_TTL_MINUTES": "15",
            "ZERO_SERVICE_TOKEN": "secret-service-token",
        }, response)

    def sender(self, url: str, token: str, body: bytes) -> tuple[int, object]:
        self.assertEqual(url, "https://accounts.svc.plus/api/internal/overlay/networks/bootstrap")
        self.assertEqual(token, "secret-service-token")
        request = json.loads(body)
        self.assertEqual(request["bootstrap"]["invite"]["expires_at"], "2030-01-01T00:15:00Z")
        self.assertNotIn("ttl_minutes", request["bootstrap"]["invite"])
        return 201, {
            "network": {"id": "net_shared"},
            "invite": {"network_id": "net_shared", "device_id": "gateway-shared", "role": "gateway",
                       "platform": "linux", "remaining_uses": 1},
            "join_uri": "xconnect://join/private-value",
        }

    def test_writes_only_private_handoff_after_exact_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment, response = self.fixture(Path(temporary))
            handoff = OWNER.execute(environment, self.sender,
                                    lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(handoff["gateway_id"], "gw-shared-0")
            self.assertEqual(response.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(response.read_text()), handoff)

    def test_rejects_response_for_another_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment, response = self.fixture(Path(temporary))
            def wrong(*_args):
                status, value = self.sender(*_args)
                value["network"]["id"] = "net_other"
                return status, value
            with self.assertRaises(OWNER.ContractError):
                OWNER.execute(environment, wrong,
                              lambda: datetime(2030, 1, 1, tzinfo=timezone.utc))
            self.assertFalse(response.exists())

    def test_http_error_is_sanitized_and_does_not_write_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment, response = self.fixture(Path(temporary))
            with self.assertRaisesRegex(OWNER.ContractError, r"HTTP 409") as caught:
                OWNER.execute(environment, lambda *_args: (409, {"secret": "must-not-leak"}))
            self.assertNotIn("must-not-leak", str(caught.exception))
            self.assertNotIn("secret-service-token", str(caught.exception))
            self.assertFalse(response.exists())

    def test_private_files_cannot_escape_runner_temp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _response = self.fixture(root)
            environment["NETWORK_RESPONSE_FILE"] = str(root.parent / "escape.json")
            with self.assertRaises(OWNER.ContractError):
                OWNER.execute(environment, self.sender)


if __name__ == "__main__":
    unittest.main()
