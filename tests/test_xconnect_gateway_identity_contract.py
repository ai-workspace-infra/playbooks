import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/vhosts/xconnect_gateway/tasks"
PLAYBOOK = (ROOT / "deploy_xconnect_gateway.yml").read_text(encoding="utf-8")


class XConnectGatewayIdentityContractTest(unittest.TestCase):
    def setUp(self):
        self.identity = (TASKS / "identity.yml").read_text(encoding="utf-8")
        self.main = (TASKS / "main.yml").read_text(encoding="utf-8")

    def test_task_files_parse(self):
        self.assertTrue(yaml.safe_load(self.identity))
        self.assertTrue(yaml.safe_load(self.main))
        self.assertTrue(yaml.safe_load(PLAYBOOK))

    def test_reconcile_and_timer_can_find_reviewed_xray(self):
        task = next(t for t in yaml.safe_load(self.main) if t.get("name") == "Reconcile signed Gateway configuration")
        self.assertIn("xconnect_gateway_xray_binary_path | dirname", task["environment"]["PATH"])
        service = (ROOT / "roles/vhosts/xconnect_gateway/templates/xconnect-gateway-sync.service.j2").read_text()
        self.assertIn('Environment="PATH={{ xconnect_gateway_xray_binary_path | dirname }}:', service)

    def test_reconcile_failure_is_reported_without_leaking_the_result(self):
        tasks = yaml.safe_load(self.main)
        names = [t.get("name") for t in tasks]
        reconcile = tasks[names.index("Reconcile signed Gateway configuration")]
        report = tasks[names.index("Report why the signed Gateway configuration did not reconcile")]
        # The command result stays hidden; only a redacted tail of its error is shown.
        self.assertIs(reconcile["no_log"], True)
        self.assertIs(reconcile["failed_when"], False)
        self.assertEqual(names.index("Report why the signed Gateway configuration did not reconcile"),
                         names.index("Reconcile signed Gateway configuration") + 1)
        self.assertIn("ansible.builtin.fail", report)
        self.assertIn("regex_replace', '[A-Za-z0-9+/_=-]{32,}', '<redacted>'", report["ansible.builtin.fail"]["msg"])
        self.assertIn("[-6:]", report["ansible.builtin.fail"]["msg"])
        self.assertEqual(report["when"], "(xconnect_gateway_up.rc | default(1)) != 0")

    def test_identity_creates_state_without_an_invitation(self):
        self.assertIn(" init", self.identity.replace("- init", " init"))
        self.assertIn('creates: "{{ xconnect_gateway_state_dir }}/state.json"', self.identity)
        self.assertNotIn("invite", self.identity)
        self.assertNotIn(" join", self.identity)

    def test_xray_destination_directory_is_created_before_binary_install(self):
        create_dir = self.identity.index("Create XConnect Gateway Xray binary directory")
        install_xray = self.identity.index("Install reviewed external Xray binary")
        self.assertLess(create_dir, install_xray)
        self.assertIn('path: "{{ xconnect_gateway_xray_binary_path | dirname }}"', self.identity)

    def test_enrollment_reuses_identity_and_needs_an_invite_only_when_not_enrolled(self):
        self.assertIn("import_tasks: identity.yml", self.main)
        self.assertIn(
            "xconnect_gateway_enrolled | bool or (xconnect_gateway_invite_file_source | trim | length > 0)",
            self.main,
        )
        # The invite is staged and consumed only for a Gateway that is not enrolled.
        self.assertEqual(self.main.count("when: not (xconnect_gateway_enrolled | default(false) | bool)"), 2)
        self.assertIn("Remove short-lived Gateway invitation", self.main)

    def test_success_requires_a_stored_credential_not_just_state_json(self):
        self.assertIn("device_credential.credential", self.main.split("Verify the Gateway holds")[1])

    def test_playbook_exposes_identity_and_enrollment_as_separate_tags(self):
        self.assertIn("tags: [xconnect-gateway-identity]", PLAYBOOK)
        self.assertIn("tasks_from: identity", PLAYBOOK)
        self.assertIn("tags: [xconnect-gateway]", PLAYBOOK)
        self.assertLess(PLAYBOOK.index("xconnect-gateway-identity"), PLAYBOOK.index("tags: [xconnect-gateway]"))


if __name__ == "__main__":
    unittest.main()
