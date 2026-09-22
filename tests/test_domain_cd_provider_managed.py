"""Contract tests for non-image provider-managed domain delivery."""

from pathlib import Path
import os
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/domain-cd-confirm-pull-only.sh"


class DomainCdProviderManagedTest(unittest.TestCase):
    def run_gate(self, domain, playbook, expect_success):
        env = os.environ.copy()
        env.update({
            "DOMAIN": domain,
            "DEPLOY_ENV": "uat",
            "TARGET_HOST": "node",
            "PLAYBOOK": playbook,
            "DEPLOY_TAG": "daily-build-2026.09.22-r2",
            "MANAGED_IMAGES": "",
            "PINNED_IMAGES": "",
        })
        result = subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, expect_success, result.stderr)
        return result

    def test_ai_workspace_openclaw_playbook_is_provider_managed(self):
        result = self.run_gate("ai-workspace", "setup-ai-workspace-rootless.yml", True)
        self.assertIn("OpenClaw Gateway", result.stdout)

    def test_other_domains_still_require_image_contract(self):
        self.run_gate("web-saas", "setup-web-saas-domain.yml", False)

    def test_wrong_ai_workspace_playbook_does_not_bypass_gate(self):
        self.run_gate("ai-workspace", "setup-ai-workspace-runtime.yml", False)


if __name__ == "__main__":
    unittest.main()
