"""Ensure the default AI Workspace path uses OpenClaw, not Hermes ACP."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AiWorkspaceDefaultProviderTest(unittest.TestCase):
    def test_rootless_path_deploys_openclaw_without_hermes(self):
        rootless = (ROOT / "setup-ai-workspace-rootless.yml").read_text()
        self.assertIn("deploy_gateway_openclaw.yml", rootless)
        self.assertNotIn("deploy_agent_hermes.yml", rootless)

    def test_bridge_playbook_does_not_enable_hermes_by_default(self):
        bridge = (ROOT / "deploy_xworkmate_bridge_vhosts.yml").read_text()
        self.assertIn("roles/vhosts/xworkmate_bridge/", bridge)
        self.assertNotIn("roles/vhosts/acp_server_hermes/", bridge)

    def test_explicit_hermes_opt_in_remains_separate(self):
        hermes = (ROOT / "deploy_agent_hermes.yml").read_text()
        self.assertIn("roles/vhosts/acp_server_hermes/", hermes)


if __name__ == "__main__":
    unittest.main()
