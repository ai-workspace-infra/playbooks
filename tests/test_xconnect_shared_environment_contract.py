from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class XConnectSharedEnvironmentContractTest(unittest.TestCase):
    def test_gateway_and_one_accept_shared_without_changing_defaults(self):
        gateway_tasks = (ROOT / "roles/vhosts/xconnect_gateway/tasks/main.yml").read_text()
        one_tasks = (ROOT / "roles/vhosts/xconnect_one/tasks/main.yml").read_text()
        gateway_defaults = (ROOT / "roles/vhosts/xconnect_gateway/defaults/main.yml").read_text()
        one_defaults = (ROOT / "roles/vhosts/xconnect_one/defaults/main.yml").read_text()

        self.assertIn("xconnect_gateway_environment in ['uat', 'prod', 'shared']", gateway_tasks)
        self.assertIn("xconnect_one_environment in ['uat', 'prod', 'shared']", one_tasks)
        self.assertRegex(gateway_defaults, r"(?m)^xconnect_gateway_environment: uat$")
        self.assertRegex(one_defaults, r"(?m)^xconnect_one_environment: uat$")


if __name__ == "__main__":
    unittest.main()
