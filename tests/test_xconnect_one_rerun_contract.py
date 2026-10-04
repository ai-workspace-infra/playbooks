import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TASKS = (ROOT / "roles/vhosts/xconnect_one/tasks/main.yml").read_text(encoding="utf-8")


class XConnectOneRerunContractTest(unittest.TestCase):
    def test_task_file_parses(self):
        self.assertTrue(yaml.safe_load(TASKS))

    def test_invitation_is_required_only_before_the_first_join(self):
        first_assert = TASKS.split("- name: Install XConnect One preflight dependencies")[0]
        self.assertNotIn("invite", first_assert)
        gate = TASKS.index("Require an invitation only for a node that has not joined yet")
        self.assertGreater(gate, TASKS.index("Inspect existing XConnect One enrollment"))
        self.assertLess(gate, TASKS.index("Join XConnect One with protected invite file"))
        self.assertIn("xconnect_one_existing_state.stat.exists or", TASKS)

    def test_join_and_invite_staging_still_skip_joined_nodes(self):
        self.assertEqual(TASKS.count("not xconnect_one_existing_state.stat.exists"), 3)


if __name__ == "__main__":
    unittest.main()
