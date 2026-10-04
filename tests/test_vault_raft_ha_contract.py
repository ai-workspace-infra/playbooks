import pathlib
import re
import unittest

try:
    import jinja2
    import yaml
except ImportError:  # Plain stdlib contract tests still run without Ansible extras.
    jinja2 = None
    yaml = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS = (ROOT / "roles/vhosts/vault/tasks/main.yml").read_text()
DEFAULTS = (ROOT / "roles/vhosts/vault/defaults/main.yml").read_text()
ROLE_VARS = (ROOT / "roles/vhosts/vault/vars/main.yml").read_text()
README = (ROOT / "roles/vhosts/vault/readme.md").read_text()


def task_block(name):
    match = re.search(
        rf"(?ms)^- name: {re.escape(name)}\n(.*?)(?=^- name: |\Z)", TASKS
    )
    if not match:
        raise AssertionError(f"Vault role task not found: {name}")
    return match.group(1)


class VaultRaftHAContractTest(unittest.TestCase):
    def test_existing_single_node_backend_remains_the_default(self):
        self.assertRegex(DEFAULTS, r"(?m)^vault_storage_backend: postgresql$")
        self.assertRegex(DEFAULTS, r"(?m)^vault_ha_enabled: false$")
        self.assertNotRegex(ROLE_VARS, r"(?m)^vault_storage_backend:")
        self.assertNotRegex(ROLE_VARS, r"(?m)^vault_ha_enabled:")

    def test_raft_configuration_has_cluster_and_retry_join_settings(self):
        config = task_block("Create Vault production configuration file")
        for expected in (
            'storage "raft"',
            "node_id =",
            "retry_join {",
            "leader_api_addr =",
            "cluster_address =",
            'cluster_addr = "{{ vault_raft_cluster_addr }}"',
        ):
            self.assertIn(expected, config)
        self.assertIn("vault_storage_backend in [\"postgresql\", \"raft\"]", config)

    def test_raft_requires_explicit_validated_member_configuration(self):
        validation = task_block("Validate Vault Raft configuration")
        self.assertIn("vault_storage_backend == 'raft'", validation)
        self.assertIn("vault_raft_expected_members | int in [1, 3]", validation)
        self.assertIn("vault_raft_expected_members | int == 1 and vault_raft_retry_join | length == 0", validation)
        self.assertIn("vault_raft_expected_members | int == 3 and vault_raft_retry_join | length > 0", validation)
        self.assertIn("vault_raft_cluster_addr is match", validation)
        self.assertIn("vault_ha_enabled | bool", validation)
        self.assertIn("ansible_swaptotal_mb | int == 0", validation)
        selection = task_block("Validate Vault storage backend selection")
        self.assertIn("vault_storage_backend == 'postgresql'", selection)
        self.assertIn("vault_storage_backend == 'raft'", selection)
        self.assertIn("vault_ha_enabled | bool", selection)
        self.assertIn("vault_storage_backend == 'raft'", task_block("Create Vault production configuration file"))

    def test_raft_mode_never_auto_initializes_unseals_or_bootstraps_root(self):
        protected_tasks = (
            "Initialize Vault (first run)",
            "Save Vault initialization keys to host",
            "Load Vault unseal keys from file",
            "Unseal Vault",
            "Create unified root token alias in Vault",
            "Bootstrap Vault admin userpass auth",
        )
        for name in protected_tasks:
            with self.subTest(task=name):
                self.assertIn("vault_storage_backend == 'postgresql'", task_block(name))

    def test_missing_legacy_key_file_fails_closed_without_truncating_storage(self):
        block = task_block("Fail closed when initialized PostgreSQL Vault has no local key file")
        self.assertIn("not vault_init_file.stat.exists", block)
        self.assertNotIn("TRUNCATE TABLE", TASKS)
        initialize = task_block("Initialize Vault (first run)")
        self.assertIn("vault_init_status.rc | int == 2", initialize)
        self.assertNotIn("not vault_init_file.stat.exists", initialize)

    def test_raft_reruns_do_not_restart_or_reseal_existing_nodes(self):
        self.assertIn("state: \"{{ 'started' if vault_storage_backend == 'raft' else 'restarted' }}\"", task_block("Start standalone Vault service"))
        guard = task_block("Require operator-controlled restart for changed active Raft node")
        self.assertIn("vault_raft_restart_pending.stat.exists", guard)
        marker = task_block("Record pending operator restart for changed active Raft node")
        self.assertIn("vault_raft_service_before.rc", marker)
        self.assertIn("vault_config_write.changed", marker)
        self.assertIn("manual unseal", guard)

    def test_single_node_raft_entrypoint_is_manual_init_and_private(self):
        entrypoint = (ROOT / "deploy_vault_single_raft.yml").read_text()
        self.assertIn("vault_raft_expected_members: 1", entrypoint)
        self.assertIn("vault_raft_retry_join: []", entrypoint)
        self.assertIn("vault_shared_private_ip", entrypoint)
        self.assertIn("vault_admin_addr: http://127.0.0.1:8200", entrypoint)
        self.assertNotIn("vault operator init", entrypoint)
        self.assertNotIn("vault operator unseal", entrypoint)

    @unittest.skipUnless(jinja2 is not None and yaml is not None, "Jinja2/PyYAML unavailable")
    def test_rendered_single_and_three_node_raft_configs(self):
        config_task = next(item for item in yaml.safe_load(TASKS) if item["name"] == "Create Vault production configuration file")
        template = jinja2.Environment().from_string(config_task["ansible.builtin.copy"]["content"])
        values = dict(
            vault_storage_backend="raft", vault_data_dir="/opt/vault/data", vault_raft_node_id="vault-prod-0",
            vault_listen_addr="0.0.0.0:8200", vault_raft_listener_cluster_addr="10.81.0.4:8201",
            vault_raft_api_addr="http://10.81.0.4:8200", vault_raft_cluster_addr="https://10.81.0.4:8201",
        )
        single = template.render(**values, vault_raft_retry_join=[])
        self.assertIn('storage "raft"', single)
        self.assertNotIn("retry_join {", single)
        three = template.render(**values, vault_raft_retry_join=[{"leader_api_addr": "http://10.81.0.2:8200"}])
        self.assertIn('leader_api_addr = "http://10.81.0.2:8200"', three)
        self.assertIn("disable_mlock = true", three)

    def test_operator_runbook_keeps_shares_and_public_cluster_ports_out_of_ci(self):
        self.assertIn("does not** run `vault operator init`", README)
        self.assertIn("never put root tokens or unseal shares", README)
        self.assertIn("TCP 8200", README)
        self.assertIn("TCP 8201", README)
        self.assertIn("never advertise the public listener as the Raft cluster", README)


if __name__ == "__main__":
    unittest.main()
