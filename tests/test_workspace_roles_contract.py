from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceRoleContractTest(unittest.TestCase):
    def test_deployment_entrypoints_require_explicit_inventory_targets(self):
        for name in ("deploy_ai_memory_hub.yml", "deploy_xworkmate_workspace.yml"):
            content = (ROOT / name).read_text()
            self.assertIn("hosts: \"{{ target_hosts | mandatory }}\"", content, name)
            self.assertIn("target_hosts not in ['all', 'localhost', '127.0.0.1']", content, name)

    def test_roles_fail_closed_instead_of_rotating_secrets_on_rerun(self):
        for role in ("ai_memory_hub", "xworkmate_workspace"):
            content = (ROOT / "roles" / role / "tasks" / "secrets.yml").read_text()
            self.assertNotIn("lookup('password'", content, role)
            self.assertIn("Require stable externally managed secrets", content, role)
            self.assertIn("no_log: true", content, role)

    def test_xworkmate_success_output_never_contains_auth_token(self):
        content = (ROOT / "roles/xworkmate_workspace/tasks/verify.yml").read_text()
        debug_task = content.split("- name: 打印部署成功卡片", 1)[1]
        self.assertNotIn("{{ xworkmate_workspace_auth_token }}", debug_task)
        self.assertIn("值已隐藏", content)


if __name__ == "__main__":
    unittest.main()
