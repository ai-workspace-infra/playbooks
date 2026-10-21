import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("node_release", Path(__file__).parents[1] / "scripts/deploy-pinned-xconnect.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseContractTest(unittest.TestCase):
    def setUp(self):
        self.contract = {"schema_version": 1, "playbooks_commit": "a" * 40,
                         "node": "node.example.com", "profile": "shared",
                         "accounts_url": "https://accounts.example.com",
                         "billing_url": "https://billing.example.com",
                         "artifacts": [{"tag": "v1.2.3", "checksum": "b" * 64}],
                         "steps": [{"phase": "validate", "playbook": "validate.yml", "vars": {}}]}

    def test_fixed_contract(self):
        self.assertEqual(module.validate(self.contract)["profile"], "shared")

    def test_reject_mutable_or_missing_artifact(self):
        for patch in ({"playbooks_commit": "main"}, {"artifacts": []},
                      {"artifacts": [{"tag": "latest", "checksum": "b" * 64}]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                module.validate(dict(self.contract, **patch))

    def test_team_scope_cannot_fall_back(self):
        with self.assertRaises(ValueError):
            module.validate(dict(self.contract, profile="team"))
        with self.assertRaises(ValueError):
            module.validate(dict(self.contract, team_id="team-a"))

    def test_no_secret_vars(self):
        contract = copy.deepcopy(self.contract)
        contract["steps"][0]["vars"] = {"agent_api_token": "must-not-be-in-gitops"}
        with self.assertRaises(ValueError):
            module.validate(contract)

    def test_validation_phase_required(self):
        contract = copy.deepcopy(self.contract)
        contract["steps"][0]["phase"] = "deploy"
        with self.assertRaises(ValueError):
            module.validate(contract)

    def test_path_escape(self):
        with self.assertRaises(ValueError):
            module.inside(Path(__file__).parent, "../scripts/deploy-pinned-xconnect.py")


if __name__ == "__main__":
    unittest.main()
