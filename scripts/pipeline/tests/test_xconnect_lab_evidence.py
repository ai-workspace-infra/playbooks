import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "xconnect-lab-evidence.py"
OWNER_SHA = "a" * 40
SPEC = importlib.util.spec_from_file_location("xconnect_lab_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeRunner:
    def __init__(self, *, ansible_returncode=0, receipt_age=12, known=True,
                 raise_ansible=False, checkout_sha=OWNER_SHA):
        self.calls = []
        self.ansible_returncode = ansible_returncode
        self.receipt_age = receipt_age
        self.known = known
        self.raise_ansible = raise_ansible
        self.checkout_sha = checkout_sha

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:2] == ["ssh-keygen", "-F"]:
            return SimpleNamespace(returncode=0 if self.known else 1, stdout="host ssh-ed25519 a2V5\n")
        if command[0] == "git":
            return SimpleNamespace(returncode=0, stdout=self.checkout_sha + "\n", stderr="")
        if command[0] == "ansible-playbook":
            if self.raise_ansible:
                raise RuntimeError("runner exception")
            variables_arg = command[command.index("--extra-vars") + 1]
            variables = json.loads(Path(variables_arg.removeprefix("@")).read_text())
            if self.ansible_returncode == 0:
                receipt = {
                    "schema": "xconnect-lab-evidence/v1",
                    "status": "verified",
                    "environment": "uat",
                    "run_id": variables["xconnect_evidence_run_id"],
                    "owner_sha": variables["xconnect_evidence_owner_sha"],
                    "network_id": variables["xconnect_evidence_network_id"],
                    "gateway_id": variables["xconnect_evidence_gateway_id"],
                    "one_device_id": variables["xconnect_evidence_one_device_id"],
                    "gateway_target": variables["xconnect_evidence_gateway_target"],
                    "one_target": variables["xconnect_evidence_one_target"],
                    "handshake_age_seconds": self.receipt_age,
                    "one_status_verified": True,
                    "gateway_cli_status_ok": True,
                    "gateway_state_binding_verified": True,
                    "gateway_credential_present": True,
                    "gateway_peer_source_verified": True,
                    "tls_sni_verified": True,
                    "private_ping_verified": True,
                    "private_http_verified": True,
                }
                path = Path(variables["xconnect_evidence_receipt_file"])
                path.write_text(json.dumps(receipt))
                path.chmod(0o600)
            return SimpleNamespace(returncode=self.ansible_returncode, stdout="", stderr="")
        raise AssertionError(command)


class EvidenceOwnerTests(unittest.TestCase):
    def environment(self, root):
        for name in ("known_hosts", "gateway.key", "one.key"):
            path = root / name
            path.write_text("fixture")
            path.chmod(0o600)
        contract = {
            "environment": "uat",
            "run_id": "xcl-123-1",
            "network_id": "net_uat",
            "known_hosts_file": str(root / "known_hosts"),
            "transport_server_name": "tw-xconnect.svc.plus",
            "private_probe_url": "http://10.77.0.1:18081/evidence",
            "private_probe_marker": "xconnect-private-evidence",
            "max_handshake_age_seconds": 180,
            "gateway": {"id": "gw-uat-lab", "target": "gateway.svc.plus", "user": "deployer",
                        "private_key_file": str(root / "gateway.key"), "overlay_ip": "10.77.0.1",
                        "state_dir": "/var/lib/xconnect-gateway", "wireguard_interface": "xconzero0"},
            "one": {"target": "one.svc.plus", "user": "deployer",
                    "private_key_file": str(root / "one.key"), "device_id": "one-lab-1",
                    "state_dir": "/var/lib/xconnect-one", "wireguard_interface": "xconone0"},
        }
        contract_path = root / "contract.json"
        contract_path.write_text(json.dumps(contract))
        contract_path.chmod(0o600)
        return {
            "RUNNER_TEMP": str(root),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "XCONNECT_EVIDENCE_OWNER_SHA": OWNER_SHA,
            "XCONNECT_EVIDENCE_ACTION_REF": OWNER_SHA,
            "XCONNECT_EVIDENCE_CONTRACT_FILE": str(contract_path),
            "XCONNECT_EVIDENCE_RECEIPT_FILE": str(root / "receipt.json"),
            "GITHUB_OUTPUT": str(root / "output"),
        }

    def test_exact_pair_produces_bound_private_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            runner = FakeRunner()
            receipt = MODULE.execute(env, runner)
            command, options = runner.calls[-1]
            self.assertEqual(command[0], "ansible-playbook")
            self.assertIn("StrictHostKeyChecking=yes", options["env"]["ANSIBLE_SSH_ARGS"])
            self.assertNotIn("accept-new", options["env"]["ANSIBLE_SSH_ARGS"])
            self.assertEqual(receipt["handshake_age_seconds"], 12)
            self.assertEqual(receipt["owner_sha"], OWNER_SHA)
            self.assertEqual((root / "receipt.json").stat().st_mode & 0o777, 0o600)
            self.assertFalse(any(path.name.endswith("inventory.json") for path in root.iterdir()))

    def test_stale_handshake_and_failed_playbook_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            with self.assertRaisesRegex(MODULE.ContractError, "stale"):
                MODULE.execute(env, FakeRunner(receipt_age=181))
            self.assertFalse(Path(env["XCONNECT_EVIDENCE_RECEIPT_FILE"]).exists())

    def test_run_owner_binding_and_runner_exception_remove_old_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            receipt = Path(env["XCONNECT_EVIDENCE_RECEIPT_FILE"])
            receipt.write_text('{"status":"verified"}')
            receipt.chmod(0o600)
            env["GITHUB_RUN_ATTEMPT"] = "2"
            with self.assertRaisesRegex(MODULE.ContractError, "current GitHub run"):
                MODULE.execute(env, FakeRunner())
            self.assertFalse(receipt.exists())
            receipt.write_text('{"status":"verified"}')
            receipt.chmod(0o600)
            env["GITHUB_RUN_ATTEMPT"] = "1"
            env["XCONNECT_EVIDENCE_ACTION_REF"] = "b" * 40
            with self.assertRaisesRegex(MODULE.ContractError, "action ref"):
                MODULE.execute(env, FakeRunner())
            self.assertFalse(receipt.exists())
            receipt.write_text('{"status":"verified"}')
            receipt.chmod(0o600)
            env["XCONNECT_EVIDENCE_ACTION_REF"] = OWNER_SHA
            with self.assertRaisesRegex(RuntimeError, "runner exception"):
                MODULE.execute(env, FakeRunner(raise_ansible=True))
            self.assertFalse(receipt.exists())
            with self.assertRaisesRegex(MODULE.ContractError, "exit 4"):
                MODULE.execute(env, FakeRunner(ansible_returncode=4))
            self.assertFalse(Path(env["XCONNECT_EVIDENCE_RECEIPT_FILE"]).exists())

    def test_local_action_binds_the_explicit_checkout_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            env["XCONNECT_EVIDENCE_ACTION_REF"] = ""
            self.assertEqual(MODULE.execute(env, FakeRunner())["owner_sha"], OWNER_SHA)
            with self.assertRaisesRegex(MODULE.ContractError, "checked-out Playbooks SHA"):
                MODULE.execute(env, FakeRunner(checkout_sha="b" * 40))

    def test_target_trust_and_private_probe_contract_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            contract_path = Path(env["XCONNECT_EVIDENCE_CONTRACT_FILE"])
            contract = json.loads(contract_path.read_text())
            contract["gateway"]["target"] = "*.svc.plus"
            contract_path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(MODULE.ContractError, "one exact host"):
                MODULE.execute(env, FakeRunner())
            contract["gateway"]["target"] = "gateway.svc.plus"
            contract["private_probe_url"] = "https://public.example/evidence"
            contract_path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(MODULE.ContractError, "high-port HTTP"):
                MODULE.execute(env, FakeRunner())
            contract["private_probe_url"] = "http://10.77.0.1:18081/evidence"
            contract_path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(MODULE.ContractError, "known_hosts"):
                MODULE.execute(env, FakeRunner(known=False))

    def test_local_addresses_cannot_prove_gateway_private_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            path = Path(env["XCONNECT_EVIDENCE_CONTRACT_FILE"])
            contract = json.loads(path.read_text())
            for address in ("127.0.0.1", "169.254.10.2", "0.0.0.0", "::1"):
                with self.subTest(address=address):
                    contract["gateway"]["overlay_ip"] = address
                    path.write_text(json.dumps(contract))
                    runner = FakeRunner()
                    with self.assertRaisesRegex(MODULE.ContractError, "routable private overlay"):
                        MODULE.execute(env, runner)
                    self.assertFalse(any(command[0] == "ansible-playbook" for command, _ in runner.calls))


if __name__ == "__main__":
    unittest.main()
