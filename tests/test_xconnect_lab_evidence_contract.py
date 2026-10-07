import unittest
from pathlib import Path
import subprocess
import tempfile
import yaml


ROOT = Path(__file__).resolve().parents[1]


class XConnectLabEvidenceContractTests(unittest.TestCase):
    def test_peer_parser_requires_unique_exact_device_and_live_public_key(self):
        document = yaml.safe_load((ROOT / "xconnect-lab-evidence.yml").read_text())
        task = next(task for play in document for task in play.get("tasks", [])
                    if task["name"] == "Bind the One public key to its exact Gateway runtime peer")
        script = task["ansible.builtin.command"]["argv"][2]
        key = "A" * 43 + "="
        other_key = "B" * 43 + "="
        valid = f"[Peer]\n# DeviceID = one-lab-1\nPublicKey = {key}\n"
        cases = [
            (valid, 0),
            (valid + valid, 1),
            (valid.replace("one-lab-1", "one-other"), 1),
            (valid.replace(key, other_key), 1),
            (valid.replace("[Peer]", "[Interface]"), 1),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            source = runtime / "exact.conf"
            for body, expected in cases:
                with self.subTest(body=body):
                    source.write_text(body)
                    result = subprocess.run(["bash", "-c", script, "test-peer", temporary, "one-lab-1", key], capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, expected == 0, result.stderr)
                    if expected == 0:
                        self.assertEqual(result.stdout.strip(), key)
            source.write_text(valid)
            (runtime / "ambiguous.conf").write_text(valid)
            result = subprocess.run(["bash", "-c", script, "test-peer", temporary, "one-lab-1", key], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_playbook_verifies_exact_runtime_and_data_plane_evidence(self):
        source = (ROOT / "xconnect-lab-evidence.yml").read_text()
        for evidence in (
            "argv: [xconnect, status", "xconnect-gateway", "latest-handshakes",
            "verify_hostname", "private overlay reachability", "private HTTP evidence",
            "xconnect-lab-evidence/v1",
        ):
            self.assertIn(evidence, source)
        self.assertIn("xconnect_evidence_one_status.device_id == xconnect_evidence_one_device_id", source)
        self.assertIn("xconnect_evidence_one_status.network_id == xconnect_evidence_network_id", source)
        self.assertIn(".gateway_id == $gateway and .network_id == $network", source)
        self.assertIn(".device_credential.credential", source)
        self.assertIn("current_device", source)
        self.assertIn("peer_matches", source)
        self.assertIn('[[ "$peer_matches" -eq 1 ]]', source)
        self.assertIn("gateway_state_binding_verified", source)
        self.assertNotIn("gateway_status_verified", source)
        self.assertNotIn("signed XConnect Gateway status", source)

    def test_playbook_has_no_cloud_or_control_plane_execution(self):
        source = (ROOT / "xconnect-lab-evidence.yml").read_text().lower()
        for forbidden in ("terraform", "cloudflare", "aws ", "vault", "accounts", "join-uri"):
            self.assertNotIn(forbidden, source)

    def test_owner_rejects_discovery_and_weak_host_key_modes(self):
        source = (ROOT / "scripts/pipeline/xconnect-lab-evidence.py").read_text()
        self.assertIn("StrictHostKeyChecking=yes", source)
        self.assertNotIn("accept-new", source)
        self.assertNotIn("describe-instances", source)
        self.assertIn('reviewed known_hosts does not contain exact target', source)


if __name__ == "__main__":
    unittest.main()
