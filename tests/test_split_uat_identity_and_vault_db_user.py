"""Static safety contracts for Akamai Debian identities and Vault PostgreSQL."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SplitUatIdentityAndVaultDbUserTest(unittest.TestCase):
    def test_ai_workspace_users_follow_explicit_user_or_ansible_user(self):
        for relative, user_var, home_var in (
            ("roles/vhosts/gateway_openclaw/defaults/main.yml", "gateway_openclaw_app_user", "gateway_openclaw_home"),
            ("roles/vhosts/xworkmate_bridge/defaults/main.yml", "xworkmate_bridge_app_user", "xworkmate_bridge_service_home"),
            ("roles/vhosts/acp_server_hermes/defaults/main.yml", "acp_hermes_app_user", "acp_hermes_home"),
        ):
            defaults = (ROOT / relative).read_text()
            tasks = (ROOT / relative.replace("defaults/main.yml", "tasks/main.yml")).read_text()
            self.assertIn(user_var, defaults)
            self.assertIn("ansible_user", defaults)
            self.assertIn("ansible.builtin.getent", tasks)
            self.assertIn("ansible_facts.getent_passwd", tasks)
            self.assertIn(home_var, tasks)
            self.assertNotRegex(defaults, r"else\s+'ubuntu'")

    def test_vault_psql_always_uses_declared_database_role(self):
        defaults = (ROOT / "roles/vhosts/vault/vars/main.yml").read_text()
        tasks = (ROOT / "roles/vhosts/vault/tasks/main.yml").read_text()
        self.assertIn("vault_postgres_database_user: vault_storage", defaults)
        self.assertIn("psql -U ' ~ vault_postgres_database_user", tasks)
        self.assertIn("psql -h 127.0.0.1 -p ' ~ (vault_pg_port | string) ~ ' -U '", tasks)
        self.assertNotRegex(tasks, r"else\s+'psql'")


if __name__ == "__main__":
    unittest.main()
