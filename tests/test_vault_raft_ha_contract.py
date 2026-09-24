import pathlib
import re
import unittest


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

    def test_raft_requires_explicit_validated_ha_configuration(self):
        validation = task_block("Validate Vault Raft HA configuration")
        self.assertIn("vault_storage_backend == 'raft'", validation)
        self.assertIn("vault_raft_retry_join | length > 0", validation)
        self.assertIn("vault_raft_cluster_addr is match", validation)
        self.assertIn("vault_ha_enabled | bool", validation)
        selection = task_block("Validate Vault storage backend selection")
        self.assertIn("vault_storage_backend == 'postgresql'", selection)
        self.assertIn("vault_storage_backend == 'raft'", selection)
        self.assertIn("vault_ha_enabled | bool", selection)

    def test_raft_mode_never_auto_initializes_unseals_or_bootstraps_root(self):
        protected_tasks = (
            "Reset Vault tables if initialized without key file (lost keys)",
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

    def test_operator_runbook_keeps_shares_and_public_cluster_ports_out_of_ci(self):
        self.assertIn("does not** run `vault operator init`", README)
        self.assertIn("never put root tokens or unseal shares", README)
        self.assertIn("TCP 8200", README)
        self.assertIn("TCP 8201", README)
        self.assertIn("never advertise the public listener as the Raft cluster", README)


if __name__ == "__main__":
    unittest.main()
