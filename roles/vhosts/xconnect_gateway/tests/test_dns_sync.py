import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "files" / "xconnect-gateway-dns-sync.py"
SPEC = importlib.util.spec_from_file_location("xconnect_gateway_dns_sync", SCRIPT)
DNS_SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DNS_SYNC)


WIREGUARD_CONFIG = """[Interface]
Address = 10.79.0.1/32

[Peer]
# DeviceID = vault-prod-1
AllowedIPs = 10.79.0.2/32

[Peer]
# DeviceID = xconnect-linux-secops-shenlan-inspiron-5415-ops
AllowedIPs = 10.79.0.7/32
"""


class DynamicDNSTests(unittest.TestCase):
    def test_generates_peer_names_and_tracks_service_alias_by_device_id(self):
        hosts = DNS_SYNC.build_hosts(
            WIREGUARD_CONFIG,
            "shared.internal",
            "vault-prod-0",
            [{"name": "internal-xworkmate-bridge.svc.plus", "device_id": "xconnect-linux-secops-shenlan-inspiron-5415-ops"}],
        )
        self.assertIn("10.79.0.1 vault-prod-0.shared.internal\n", hosts)
        self.assertIn("10.79.0.2 vault-prod-1.shared.internal\n", hosts)
        self.assertIn(
            "10.79.0.7 internal-xworkmate-bridge.svc.plus xconnect-linux-secops-shenlan-inspiron-5415-ops.shared.internal\n",
            hosts,
        )

    def test_service_alias_disappears_when_the_one_is_absent_from_signed_config(self):
        hosts = DNS_SYNC.build_hosts(
            "[Interface]\nAddress = 10.79.0.1/32\n",
            "shared.internal",
            "vault-prod-0",
            [{"name": "internal-xworkmate-bridge.svc.plus", "device_id": "secops-one"}],
        )
        self.assertNotIn("internal-xworkmate-bridge.svc.plus", hosts)

    def test_rejects_invalid_address_and_ambiguous_dns_names(self):
        with self.assertRaisesRegex(ValueError, "IPv4 /32"):
            DNS_SYNC.parse_wireguard_config("[Interface]\nAddress=10.79.0.1/24\n")
        ambiguous = WIREGUARD_CONFIG + "\n[Peer]\n# DeviceID = vault_prod_1\nAllowedIPs = 10.79.0.8/32\n"
        with self.assertRaisesRegex(ValueError, "maps to more than one"):
            DNS_SYNC.build_hosts(ambiguous, "shared.internal", "vault-prod-0", [])

    def test_hosts_file_is_replaced_only_when_records_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run" / "dns.hosts"
            self.assertTrue(DNS_SYNC.write_if_changed(path, "10.79.0.1 vault-prod-0.shared.internal\n"))
            self.assertFalse(DNS_SYNC.write_if_changed(path, "10.79.0.1 vault-prod-0.shared.internal\n"))
            self.assertEqual(path.read_text(), "10.79.0.1 vault-prod-0.shared.internal\n")


if __name__ == "__main__":
    unittest.main()
