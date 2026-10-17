from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).parents[1]


class SMTPReleaseContractTest(unittest.TestCase):
    def test_normal_release_persists_before_pin_and_authenticates_after(self):
        tasks = yaml.safe_load((ROOT / "pin-prod-v2-doco-release.yml").read_text())[0]["tasks"]
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Resolve and persist SMTP from the same immutable release"),
                        names.index("Pin the controller to the new release while preserving credentials"))
        self.assertGreater(names.index("Require authenticated SMTP after Doco-CD reconciliation"),
                           names.index("Reconcile only the deployment controller"))

    def test_credentials_are_fixed_versioned_and_never_logged(self):
        tasks = yaml.safe_load((ROOT / "tasks/prod-v2-smtp.yml").read_text())
        read = next(task for task in tasks if task["name"].startswith("Read the release-pinned"))
        self.assertTrue(read["no_log"])
        self.assertEqual(read["delegate_to"], "localhost")
        self.assertEqual(read["ansible.builtin.command"]["argv"][4], "{{ item.version | string }}")
        required = next(task for task in tasks if task["name"].startswith("Require fixed secret"))
        self.assertIn("^[1-9][0-9]*$", str(required))
        writer = next(task for task in tasks if task["name"].startswith("Persist Accounts SMTP"))
        self.assertTrue(writer["no_log"])
        self.assertEqual(writer["ansible.builtin.template"]["dest"], "/etc/xcontrol/web-saas/accounts-smtp.env")


if __name__ == "__main__":
    unittest.main()
