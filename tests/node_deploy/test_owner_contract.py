import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]


class OwnerContractTests(unittest.TestCase):
    def test_vault_stage_resolves_only_owner_scripts(self):
        action = yaml.safe_load((ROOT / ".github/actions/vault-node-stage/action.yml").read_text())
        source = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
        self.assertIn("${NODE_OWNER_ROOT}/scripts/node_deploy/verify_vault_stage.py", source)
        self.assertNotIn("${GITHUB_WORKSPACE}/scripts/node_deploy", source)

    def test_package_policy_preserves_legacy_default(self):
        action = yaml.safe_load((ROOT / ".github/actions/setup-deployment-runner/action.yml").read_text())
        self.assertEqual(
            action["inputs"]["package_init_policy"]["default"],
            "disable-unattended-upgrades",
        )
        source = (ROOT / ".github/actions/setup-deployment-runner/scripts/setup.sh").read_text()
        self.assertIn("disable-unattended-upgrades)", source)
        self.assertIn("preserve)", source)

    def test_existing_adapter_has_no_vault_authorization(self):
        source = (ROOT / ".github/actions/node-contract-existing/action.yml").read_text()
        self.assertIn("legacy_source.py", source)
        self.assertNotIn("auth/jwt/login", source)
        self.assertNotIn("signed_key", source)


if __name__ == "__main__":
    unittest.main()
