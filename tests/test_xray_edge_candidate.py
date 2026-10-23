import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess


SCRIPT = Path(__file__).parents[1] / "roles/vhosts/xray-edge/files/validate_candidate.py"
SPEC = importlib.util.spec_from_file_location("validate_candidate", SCRIPT)
validate_candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_candidate)


class CandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.main = self.root / "Caddyfile"
        self.conf_dir = self.root / "conf.d"
        self.conf_dir.mkdir()
        self.main.write_text("{\n    admin 127.0.0.1:2019\n}\n\nimport conf.d/*.caddy\n", encoding="utf-8")
        (self.conf_dir / "business.caddy").write_text("business.example { respond 200 }\n", encoding="utf-8")
        (self.conf_dir / "xray-edge.caddy").write_text("old.example { respond 404 }\n", encoding="utf-8")
        self.candidate = self.root / "candidate"
        self.candidate.write_text("edge.example { respond 404 }\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_shadow_contains_other_sites_and_replaces_only_managed_site(self):
        shadow = validate_candidate.shadow_config(
            self.main, self.conf_dir, "xray-edge.caddy", self.candidate
        )
        self.assertIn(str((self.conf_dir / "business.caddy").resolve()), shadow)
        self.assertIn(str(self.candidate.resolve()), shadow)
        self.assertNotIn("old.example", shadow)
        self.assertIn("admin 127.0.0.1:2019", shadow)
        self.assertEqual(self.main.read_text(encoding="utf-8").count("import conf.d/*.caddy"), 1)

    def test_missing_expected_import_fails_before_running_caddy(self):
        self.main.write_text("{\n    admin 127.0.0.1:2019\n}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not import"):
            validate_candidate.shadow_config(
                self.main, self.conf_dir, "xray-edge.caddy", self.candidate
            )

    def test_caddy_validation_failure_is_returned_without_config_output(self):
        with patch.object(
            validate_candidate.subprocess,
            "run",
            return_value=CompletedProcess(["caddy"], 1),
        ) as run:
            result = validate_candidate.validate(
                self.main, self.conf_dir, "xray-edge.caddy", self.candidate, "caddy"
            )
        self.assertEqual(result, 1)
        args = run.call_args.args[0]
        shadow = Path(args[-1])
        self.assertFalse(shadow.exists())


if __name__ == "__main__":
    unittest.main()
