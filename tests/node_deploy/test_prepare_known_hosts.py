import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "node_deploy"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("prepare_known_hosts", SCRIPT_DIR / "prepare_known_hosts.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class KnownHostPinTests(unittest.TestCase):
    def test_accepts_only_the_reviewed_key_for_exact_address(self):
        result = module.verify_scan("35.1.2.3", "abc=", "# banner\n35.1.2.3 ssh-ed25519 abc=\n")
        self.assertEqual(result, "35.1.2.3 ssh-ed25519 abc=")
        with self.assertRaises(ValueError):
            module.verify_scan("35.1.2.3", "abc=", "35.1.2.3 ssh-ed25519 other=\n")
        with self.assertRaises(ValueError):
            module.verify_scan("35.1.2.3", "abc=", "35.1.2.4 ssh-ed25519 abc=\n")

    def test_non_default_port_uses_bracketed_known_hosts_name(self):
        label = module.host_label("vps.example.net", 2222)
        self.assertEqual(label, "[vps.example.net]:2222")
        scan = "[vps.example.net]:2222 ssh-ed25519 abc=\n"
        self.assertEqual(module.verify_scan(label, "abc=", scan), "[vps.example.net]:2222 ssh-ed25519 abc=")
        self.assertEqual(module.host_label("35.1.2.3", 22), "35.1.2.3")


if __name__ == "__main__":
    unittest.main()
