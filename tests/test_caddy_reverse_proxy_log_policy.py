import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "roles/host/xconnect_node_log_policy/files/apply_caddy_log_policy.py"
)
SPEC = importlib.util.spec_from_file_location("caddy_log_policy", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CaddyLogPolicyTests(unittest.TestCase):
    def test_creates_global_block_when_absent(self):
        rendered, changed = MODULE.render("example.org {\n\trespond 200\n}\n")
        self.assertTrue(changed)
        self.assertTrue(rendered.startswith("{\n"))
        self.assertIn("level ERROR", rendered)

    def test_inserts_into_existing_global_block(self):
        rendered, changed = MODULE.render("{\n\tadmin off\n}\n\nexample.org {\n}\n")
        self.assertTrue(changed)
        self.assertIn("{\n# BEGIN XConnect reverse_proxy log policy", rendered)
        self.assertIn("\tadmin off\n}", rendered)

    def test_is_idempotent_for_its_marker(self):
        original = "{\n" + MODULE.BLOCK + "\n}\n"
        rendered, changed = MODULE.render(original)
        self.assertFalse(changed)
        self.assertEqual(original, rendered)

    def test_does_not_duplicate_existing_reverse_proxy_log_config(self):
        original = '{\n\tlog rp {\n\t\tinclude http.handlers.reverse_proxy\n\t}\n}\n'
        rendered, changed = MODULE.render(original)
        self.assertFalse(changed)
        self.assertEqual(original, rendered)

    def test_refuses_to_overwrite_existing_global_log_directives(self):
        with self.assertRaisesRegex(ValueError, "already defines log directives"):
            MODULE.render("{\n\tlog {\n\t\toutput stderr\n\t}\n}\n")

    def test_refuses_unclosed_global_block(self):
        with self.assertRaisesRegex(ValueError, "not closed"):
            MODULE.render("{\n\tadmin off\n")


if __name__ == "__main__":
    unittest.main()
