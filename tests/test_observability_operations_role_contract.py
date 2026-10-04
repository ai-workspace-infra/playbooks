"""Guard the boundary and fail-closed operation contract for Observability."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/docker/observability_server_operations"


class ObservabilityOperationsRoleTests(unittest.TestCase):
    def test_entrypoint_is_local_and_dispatches_the_service_role(self):
        playbook = yaml.safe_load((ROOT / "observability_operations.yml").read_text())
        self.assertEqual(playbook[0]["hosts"], "localhost")
        self.assertEqual(playbook[0]["connection"], "local")
        self.assertIn("docker/observability_server_operations", playbook[0]["roles"])

    def test_role_is_uat_only_and_allows_only_reviewed_operations(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        rendered = str(tasks)
        self.assertIn("observability_operations_environment == 'uat'", rendered)
        self.assertIn("data_migrate", rendered)
        self.assertIn("verify_store", rendered)
        self.assertIn("verify_target", rendered)
        self.assertIn("verify_mcp", rendered)

    def test_migration_scripts_are_colocated_and_no_longer_depend_on_toolkit_paths(self):
        scripts = list((ROLE / "files").glob("observability_*.sh"))
        self.assertEqual(len(scripts), 6)
        migrate = (ROLE / "files/observability_data_migrate.sh").read_text()
        self.assertIn('"${script_dir}/observability_data_source_stage.sh"', migrate)
        self.assertIn('"${script_dir}/observability_data_target_restore.sh"', migrate)
        self.assertNotIn(".github/scripts/observability/", "\n".join(path.read_text() for path in scripts))


if __name__ == "__main__":
    unittest.main()
