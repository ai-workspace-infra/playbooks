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

    def test_snapshot_is_not_replayed_as_a_raft_operator_action(self):
        action = yaml.safe_load((ROOT / ".github/actions/vault-node-stage/action.yml").read_text())
        step = next(step for step in action["runs"]["steps"] if step["name"] == "Run the stage action")
        self.assertIn("inputs.action != 'snapshot'", step["if"])

    def test_migration_observation_resolves_only_the_owner_script(self):
        action = yaml.safe_load((ROOT / ".github/actions/vault-migration-observation/action.yml").read_text())
        source = "\n".join(step.get("run", "") for step in action["runs"]["steps"])
        self.assertIn("${NODE_OWNER_ROOT}/scripts/node_deploy/vault_migration_observation.py", source)
        self.assertNotIn("stage_plan.py", source)
        self.assertEqual(set(action["outputs"]), {"recommended_stage", "blocked", "facts"})

    def test_node_stage_runner_keeps_exact_runtime_defaults(self):
        action = yaml.safe_load((ROOT / ".github/actions/setup-node-stage-runner/action.yml").read_text())
        self.assertEqual(action["inputs"]["pyyaml_version"]["default"], "6.0.2")
        self.assertEqual(action["inputs"]["ansible_core_version"]["default"], "2.17.14")
        self.assertEqual(action["inputs"]["ansible_posix_version"]["default"], "2.1.0")


if __name__ == "__main__":
    unittest.main()
