import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS = (ROOT / "roles/vhosts/vector-agent/tasks/main.yml").read_text()


class VectorAgentContractTest(unittest.TestCase):
    def test_rendered_config_is_validated_before_service_start(self):
        validation = TASKS.index("vector validate --no-environment --config-toml")
        service_start = TASKS.index("Enable and start Vector service with safe failure diagnostics")
        self.assertLess(validation, service_start)

    def test_validation_and_start_failures_redact_credentials(self):
        self.assertIn("<redacted>", TASKS)
        self.assertIn("Authorization", TASKS)
        self.assertIn("journalctl -u vector.service", TASKS)

    def test_service_start_diagnostics_do_not_disable_existing_restart_handler(self):
        self.assertIn("notify: Restart vector", TASKS)
        self.assertIn("state: started", TASKS)


if __name__ == "__main__":
    unittest.main()
