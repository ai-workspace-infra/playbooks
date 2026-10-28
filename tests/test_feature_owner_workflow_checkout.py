"""Reusable feature owner code must match its selected workflow revision."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class FeatureOwnerCheckoutTest(unittest.TestCase):
    def test_owner_checkout_uses_called_workflow_revision(self):
        for name in ("dev-feature-flux.yml", "dev-web-saas-seed.yml"):
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
                steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
                checkouts = [step["with"] for step in steps
                             if step.get("uses", "").startswith("actions/checkout@")
                             and step.get("with", {}).get("repository") == "ai-workspace-infra/playbooks"]
                self.assertEqual(len(checkouts), 1)
                self.assertEqual(checkouts[0]["ref"], "${{ job.workflow_sha }}")
                self.assertIs(checkouts[0]["persist-credentials"], False)


if __name__ == "__main__":
    unittest.main()
