#!/usr/bin/env python3
"""Regression checks for the managed Agent Proxy source checkout."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "roles/vhosts/agent-proxy/tasks/main.yml"


class AgentProxySourceCheckoutTest(unittest.TestCase):
    def test_git_fetches_force_refresh_repointed_tags(self) -> None:
        content = TASKS.read_text()

        for task_name in (
            "Clone agent.svc.plus repository",
            "Fallback to main branch if specified version is not found",
        ):
            task = re.search(
                rf"- name: {re.escape(task_name)}\n(?P<body>.*?)(?=\n- name:|\Z)",
                content,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(task, f"missing task: {task_name}")
            self.assertRegex(task.group("body"), r"(?m)^\s+force: true$")


if __name__ == "__main__":
    unittest.main()
