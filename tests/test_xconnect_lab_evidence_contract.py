import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class XConnectLabEvidenceContractTests(unittest.TestCase):
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
