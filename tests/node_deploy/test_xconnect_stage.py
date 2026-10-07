import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "node_deploy"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("xconnect_stage", SCRIPT_DIR / "xconnect_stage.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

GATEWAY_KEY = "B" * 43 + "="
TOPOLOGY = {
    "apiVersion": "gitops.svc.plus/v1alpha1",
    "kind": "XConnectOneNodeSet",
    "metadata": {"name": "xconnect-vault-shared", "environment": "shared"},
    "spec": {
        "network": {
            "id": "net_shared_vault",
            "cidr": "10.79.0.0/24",
            "gateway_wireguard_address": "10.79.0.1/32",
            "transport_profile": {
                "kind": "vless-xhttp", "port": 443, "path": "/xconnect", "mode": "auto",
                "host": "vault-xconnect.svc.plus", "frontend": "caddy-unix-h2c",
                "listen_socket": "/run/xconnect-gateway/xray.sock",
            },
        },
        "control_plane": {"accounts_api_url": "https://accounts.svc.plus"},
        "dns": {
            "enabled": True,
            "interface": "xconone0",
            "listen_address": "10.79.0.1",
            "zone": "shared.internal",
            "domains": ["shared.internal", "svc.plus"],
            "upstream_servers": ["1.1.1.1", "8.8.8.8"],
            "records": [{"name": "internal-xworkmate-bridge.svc.plus", "device_id": "secops-one"}],
        },
        "runtime": {"gateway_state_dir": "/var/lib/xconnect-gateway/shared", "sync_interval_seconds": 300,
                    "state_dir_prefix": "/var/lib/xconnect-one", "wireguard_interface": "xconone0",
                    "xray_loopback_udp_port": 51830},
        "gateway": {"id": "vault-prod-0"},
    },
}
CONTRACT = {"spec": {"nodes": [
    {"id": "vault-prod-0", "groups": ["vault_shared_nodes", "vault_shared_leader", "xconnect_gateway"]},
    {"id": "vault-prod-1", "groups": ["vault_shared_nodes", "vault_shared_peers", "xconnect_one"]},
]}}
ENV = {"ZERO_SERVICE_TOKEN": "service-token", "ZERO_OWNER_EMAIL": "ops@example.com", "XCONNECT_VLESS_ID": "vless-id"}


class HTTPClientTests(unittest.TestCase):
    def test_identifies_the_automation_client_to_the_edge(self):
        class Reply(io.BytesIO):
            status = 201
        with patch.object(module.urllib.request, "urlopen", return_value=Reply(b'{"ok":true}')) as call:
            self.assertEqual(module.post_json("https://accounts.svc.plus/test", "secret", {}), (201, {"ok": True}))
        request = call.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), module.HTTP_USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_edge_rejection_is_not_misreported_as_owner_denial(self):
        error = HTTPError("https://accounts.svc.plus/test", 403, "Forbidden", {}, io.BytesIO(b"error code: 1010\n"))
        with patch.object(module.urllib.request, "urlopen", side_effect=error):
            status, body = module.post_json(error.url, "secret", {})
        self.assertEqual(status, 403)
        self.assertEqual(body, {"diagnostic": "cloudflare_browser_integrity_rejection"})

    def test_application_error_diagnostic_does_not_echo_secrets(self):
        error = HTTPError("https://accounts.svc.plus/test", 403, "Forbidden", {},
                          io.BytesIO(json.dumps({"error": "forbidden", "token": "secret"}).encode()))
        with patch.object(module.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(module.post_json(error.url, "secret", {}), (403, {"diagnostic": "forbidden"}))


def topology():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "topology.yaml"
        path.write_text(yaml.safe_dump(TOPOLOGY), encoding="utf-8")
        return module.load_topology(path)


def response(**overrides):
    body = {
        "network": {"id": "net_shared_vault"},
        "invite": {"network_id": "net_shared_vault", "device_id": "vault-prod-0", "role": "gateway",
                   "platform": "linux", "remaining_uses": 1},
        "join_uri": "xconnect://join/abc123",
    }
    body["invite"].update(overrides)
    return body


class TopologyTests(unittest.TestCase):
    def test_reads_the_gitops_declaration(self):
        declared = topology()
        self.assertEqual(declared["gateway_id"], "vault-prod-0")
        self.assertEqual(declared["transport_host"], "vault-xconnect.svc.plus")
        self.assertEqual(declared["controller"], "https://accounts.svc.plus")
        self.assertEqual(declared["frontend"], "caddy-unix-h2c")
        self.assertIs(declared["dns_enabled"], True)
        self.assertEqual(declared["dns_listen_address"], "10.79.0.1")
        self.assertEqual(declared["dns_records"], [{"name": "internal-xworkmate-bridge.svc.plus", "device_id": "secops-one"}])

    def test_rejects_a_non_https_controller(self):
        bad = yaml.safe_load(yaml.safe_dump(TOPOLOGY))
        bad["spec"]["control_plane"]["accounts_api_url"] = "http://accounts.svc.plus"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.yaml"
            path.write_text(yaml.safe_dump(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "https"):
                module.load_topology(path)

    def test_rejects_non_ip_overlay_dns_upstream(self):
        bad = yaml.safe_load(yaml.safe_dump(TOPOLOGY))
        bad["spec"]["dns"]["upstream_servers"] = ["resolver.example"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.yaml"
            path.write_text(yaml.safe_dump(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "upstream_servers"):
                module.load_topology(path)

    def test_rejects_static_dns_addresses_for_joined_one_aliases(self):
        bad = yaml.safe_load(yaml.safe_dump(TOPOLOGY))
        bad["spec"]["dns"]["records"] = [{"name": "secops.shared.internal", "address": "10.79.0.7"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.yaml"
            path.write_text(yaml.safe_dump(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "device_id"):
                module.load_topology(path)


class InvitationTests(unittest.TestCase):
    def test_request_binds_the_gateway_key_and_device(self):
        body = module.bootstrap_request(topology(), "gateway", "vault-prod-0", GATEWAY_KEY, "ops@example.com",
                                        "vless-id", "2026-09-25T05:00:00Z")
        network = body["bootstrap"]["network"]
        self.assertEqual(network["gateway_wireguard_public_key"], GATEWAY_KEY)
        self.assertEqual(network["transport_server_name"], "vault-xconnect.svc.plus")
        self.assertEqual(network["gateway_id"], "vault-prod-0")
        self.assertEqual((network["gateway_frontend"], network["gateway_listen_socket"]),
                         ("caddy-unix-h2c", "/run/xconnect-gateway/xray.sock"))
        self.assertEqual(body["bootstrap"]["invite"],
                         {"device_id": "vault-prod-0", "platform": "linux", "role": "gateway",
                          "expires_at": "2026-09-25T05:00:00Z"})

    def test_binding_mismatch_fails_closed(self):
        declared = topology()
        self.assertEqual(module.check_invite(response(), declared, "gateway", "vault-prod-0"), "xconnect://join/abc123")
        for override in ({"device_id": "vault-prod-1"}, {"role": "one"}, {"remaining_uses": 2}, {"network_id": "net_uat"}):
            with self.assertRaisesRegex(ValueError, "not bound"):
                module.check_invite(response(**override), declared, "gateway", "vault-prod-0")
        broken = response()
        broken["join_uri"] = "https://example.com"
        with self.assertRaisesRegex(ValueError, "join URI"):
            module.check_invite(broken, declared, "gateway", "vault-prod-0")

    def test_issued_invitation_is_a_private_file_and_never_returned(self):
        calls = []

        def post(url, token, body):
            calls.append((url, token, body))
            return 201, response()

        with tempfile.TemporaryDirectory() as directory:
            path = module.issue_invite(topology(), "gateway", "vault-prod-0", GATEWAY_KEY, Path(directory) / "s", ENV, post)
            self.assertEqual(path.read_text(), "xconnect://join/abc123\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(calls[0][0], "https://accounts.svc.plus/api/internal/overlay/networks/bootstrap")
        self.assertEqual(calls[0][1], "service-token")

    def test_http_errors_and_missing_secrets_stop_the_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "HTTP 409"):
                module.issue_invite(topology(), "gateway", "vault-prod-0", GATEWAY_KEY, Path(directory), ENV,
                                    lambda *_: (409, {}))
            with self.assertRaisesRegex(ValueError, "ZERO_SERVICE_TOKEN"):
                module.issue_invite(topology(), "gateway", "vault-prod-0", GATEWAY_KEY, Path(directory),
                                    {**ENV, "ZERO_SERVICE_TOKEN": ""}, lambda *_: (201, response()))


class VarsTests(unittest.TestCase):
    def test_gateway_vars_come_from_gitops_and_the_runner_private_dirs(self):
        values = module.gateway_vars(topology(), CONTRACT, Path("/r/bin"), Path("/r/secrets"), Path("/r/secrets/g.invite"))
        self.assertEqual(values["xconnect_gateway_id"], "vault-prod-0")
        self.assertEqual(values["xconnect_gateway_environment"], "shared")
        self.assertEqual(values["xconnect_gateway_binary_source"], "/r/bin/xconnect-gateway")
        self.assertEqual(values["xconnect_gateway_trust_bundle_source"], "/r/secrets/trust-bundle.pem")
        self.assertIs(values["xconnect_gateway_dns_enabled"], True)
        self.assertEqual(values["xconnect_gateway_dns_records"], [{"name": "internal-xworkmate-bridge.svc.plus", "device_id": "secops-one"}])
        self.assertEqual(values["xconnect_gateway_invite_file_source"], "/r/secrets/g.invite")
        self.assertEqual(module.gateway_vars(topology(), CONTRACT, Path("/b"), Path("/s"), None)
                         ["xconnect_gateway_invite_file_source"], "")

    def test_contract_and_topology_must_name_the_same_gateway(self):
        other = {"spec": {"nodes": [{"id": "vault-prod-9", "groups": ["xconnect_gateway"]}]}}
        with self.assertRaisesRegex(ValueError, "not the topology Gateway"):
            module.gateway_vars(topology(), other, Path("/b"), Path("/s"), None)


class OneTests(unittest.TestCase):
    def test_one_vars_pick_each_hosts_own_invitation(self):
        values = module.one_vars(topology(), Path("/r/bin"), Path("/r/secrets"),
                                 {"vault-prod-2": Path("/r/secrets/vault-prod-2.invite")})
        self.assertEqual(values["xconnect_one_state_dir"], "/var/lib/xconnect-one/shared")
        self.assertEqual(values["xconnect_one_binary_source"], "/r/bin/xconnect")
        self.assertEqual(values["xconnect_one_expected_network_id"], "net_shared_vault")
        self.assertEqual(values["xconnect_one_expected_xray_loopback_port"], 51830)
        self.assertIs(values["xconnect_one_dns_enabled"], True)
        self.assertEqual(values["xconnect_one_dns_server"], "10.79.0.1")
        self.assertEqual(values["xconnect_one_invite_files"], {"vault-prod-2": "/r/secrets/vault-prod-2.invite"})
        self.assertEqual(values["xconnect_one_device_id"], "{{ inventory_hostname }}")
        self.assertIn("xconnect_one_invite_files", values["xconnect_one_invite_file_source"])
        self.assertIs(values["xconnect_one_install_observability"], False)
        # Only the migration source leaves another XConnect network first.
        self.assertEqual(values["xconnect_one_release_foreign_overlays"], "{{ 'vault_legacy_source' in group_names }}")

    def test_invitations_only_for_nodes_that_have_not_joined(self):
        contract = {"spec": {"nodes": [
            {"id": "vault-prod-0", "groups": ["xconnect_gateway"]},
            {"id": "vault-prod-1", "groups": ["xconnect_one"]},
            {"id": "vault-prod-2", "groups": ["xconnect_one"]},
            {"id": "legacy", "groups": ["vault_legacy_source", "xconnect_one"]},
        ]}}
        states = {
            ("vault-prod-0", "/g"): {"gateway": {"exists": True, "enrolled": True, "public_key": GATEWAY_KEY}},
            ("vault-prod-1", "/var/lib/xconnect-one/shared/state.json"): {"gateway": {"exists": True}},
            ("vault-prod-2", "/var/lib/xconnect-one/shared/state.json"): {"gateway": {"exists": False}},
            ("legacy", "/var/lib/xconnect-one/shared/state.json"): {"gateway": {"exists": False}},
        }
        issued = []
        original_probe, original_issue = module.probe, module.issue_invite
        try:
            module.probe = lambda node, key, hosts, path: states[(node["id"], path)]
            module.issue_invite = lambda topo, role, device, gateway_key, directory, env: (
                issued.append((role, device, gateway_key)) or Path(f"/s/{device}.invite"))
            invites = module.one_invites(topology(), contract, Path("k"), Path("h"), "/g", Path("/s"))
        finally:
            module.probe, module.issue_invite = original_probe, original_issue
        self.assertEqual(sorted(invites), ["legacy", "vault-prod-2"])
        self.assertEqual(issued, [("one", "vault-prod-2", GATEWAY_KEY), ("one", "legacy", GATEWAY_KEY)])

    def test_one_waits_for_an_enrolled_gateway(self):
        contract = {"spec": {"nodes": [{"id": "vault-prod-0", "groups": ["xconnect_gateway"]}]}}
        original = module.probe
        try:
            module.probe = lambda *_: {"gateway": {"exists": True, "enrolled": False, "public_key": GATEWAY_KEY}}
            with self.assertRaisesRegex(ValueError, "enroll the XConnect Gateway"):
                module.enrolled_gateway_key(contract, Path("k"), Path("h"), "/g")
        finally:
            module.probe = original


class OperatorInviteTests(unittest.TestCase):
    DOC = {"spec": {"operator_devices": [{
        "id": "xconnect-darwin-haitaodemacbook-pro.local", "enrollment": "short-lived-single-use-invite",
    }]}}
    VAULT_ENV = {**ENV, "VAULT_ADDR": "https://vault.example", "VAULT_TOKEN": "vault-token"}

    def test_invitation_is_bound_to_the_declared_device_and_written_only_to_vault(self):
        posted, written = [], []

        def post(url, token, body):
            posted.append(body)
            reply = response(device_id="xconnect-darwin-haitaodemacbook-pro.local", role="one", platform="darwin")
            return 201, reply

        def write(addr, token, path, data):
            written.append((addr, token, path, data))
            return 204

        result = module.operator_invite(topology(), self.DOC, GATEWAY_KEY, "kv/data/CICD/shared/xconnect-operator-invite",
                                        self.VAULT_ENV, post, write)
        self.assertEqual(posted[0]["bootstrap"]["invite"]["platform"], "darwin")
        self.assertEqual(posted[0]["bootstrap"]["invite"]["role"], "one")
        self.assertEqual(written[0][2], "kv/data/CICD/shared/xconnect-operator-invite")
        self.assertEqual(written[0][3]["join_uri"], "xconnect://join/abc123")
        # The returned summary (printed to the job summary) never contains the join URI.
        self.assertNotIn("join_uri", result)
        self.assertNotIn("xconnect://", str(result))

    def test_refuses_bad_paths_missing_vault_login_and_failed_writes(self):
        ok_post = lambda *_: (201, response(device_id="xconnect-darwin-haitaodemacbook-pro.local", role="one",
                                            platform="darwin"))
        with self.assertRaisesRegex(ValueError, "invalid Vault KV path"):
            module.operator_invite(topology(), self.DOC, GATEWAY_KEY, "secret/../x", self.VAULT_ENV, ok_post)
        with self.assertRaisesRegex(ValueError, "VAULT_TOKEN"):
            module.operator_invite(topology(), self.DOC, GATEWAY_KEY, "kv/data/a", ENV, ok_post)
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            module.operator_invite(topology(), self.DOC, GATEWAY_KEY, "kv/data/a", self.VAULT_ENV, ok_post,
                                   lambda *_: 403)

    def test_exactly_one_well_formed_operator_device(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            module.operator_device({"spec": {"operator_devices": []}})
        with self.assertRaisesRegex(ValueError, "single-use"):
            module.operator_device({"spec": {"operator_devices": [{"id": "mac", "enrollment": "short-lived-single-use-invite"}]}})
        self.assertEqual(module.operator_device(self.DOC), ("xconnect-darwin-haitaodemacbook-pro.local", "darwin"))


class ArchitectureTests(unittest.TestCase):
    def test_one_architecture_for_all_targets(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(module.architecture(nodes, {"a": {"machine": "x86_64"}, "b": {"machine": "amd64"}}), "amd64")
        with self.assertRaisesRegex(ValueError, "disagree"):
            module.architecture(nodes, {"a": {"machine": "x86_64"}, "b": {"machine": "aarch64"}})
        with self.assertRaisesRegex(ValueError, "unknown"):
            module.architecture(nodes, {"a": {}, "b": {"machine": "x86_64"}})


if __name__ == "__main__":
    unittest.main()
