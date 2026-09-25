import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = (ROOT / "deploy_vault_shared_services.yml").read_text()
RAFT = (ROOT / "deploy_vault_shared_raft.yml").read_text()
VAULT_TASKS = (ROOT / "roles/vhosts/vault/tasks/main.yml").read_text()
NODE_EXPORTER_META = (ROOT / "roles/vhosts/node_exporter/meta/main.yml").read_text()
PROCESS_EXPORTER_META = (ROOT / "roles/vhosts/process_exporter/meta/main.yml").read_text()
NODE_EXPORTER_TASKS = (ROOT / "roles/vhosts/node_exporter/tasks/main.yml").read_text()
PROCESS_EXPORTER_TASKS = (ROOT / "roles/vhosts/process_exporter/tasks/main.yml").read_text()
VECTOR_TASKS = (ROOT / "roles/vhosts/vector-agent/tasks/main.yml").read_text()


class VaultSharedServiceEntrypointTest(unittest.TestCase):
    def test_entrypoint_composes_existing_reviewed_roles(self):
        for playbook in (
            "deploy_vault_shared_raft.yml",
            "deploy_xconnect_gateway.yml",
            "deploy_xconnect_one.yml",
        ):
            with self.subTest(playbook=playbook):
                self.assertIn(playbook, ENTRYPOINT)
        for role in (
            "vhosts/node_exporter",
            "vhosts/process_exporter",
            "vhosts/vector-agent",
        ):
            with self.subTest(role=role):
                self.assertIn(role, ENTRYPOINT)

    def test_vault_leader_and_peers_are_independently_selectable(self):
        self.assertIn("vault-shared-leader", ENTRYPOINT)
        self.assertIn("vault-shared-peers", ENTRYPOINT)
        self.assertIn("vault_shared_leader_group", RAFT)
        self.assertIn("vault_shared_peer_group", RAFT)
        self.assertEqual(RAFT.count("vault_admin_addr: http://127.0.0.1:8200"), 2)

    def test_shared_monitoring_can_skip_host_wide_common_dependency(self):
        for role_meta in (NODE_EXPORTER_META, PROCESS_EXPORTER_META):
            self.assertIn("vault_shared_skip_common", role_meta)
        self.assertIn("not ansible_check_mode", NODE_EXPORTER_TASKS)
        self.assertIn("not ansible_check_mode", PROCESS_EXPORTER_TASKS)
        self.assertIn("when: not ansible_check_mode", VECTOR_TASKS)

    def test_xconnect_playbooks_have_separate_selectable_tags(self):
        self.assertIn("tags: [xconnect-gateway]", (ROOT / "deploy_xconnect_gateway.yml").read_text())
        self.assertIn("tags: [xconnect-one]", (ROOT / "deploy_xconnect_one.yml").read_text())

    def test_raft_uses_private_cluster_addresses_and_never_initializes_or_unseals(self):
        self.assertIn("hostvars[inventory_hostname].vault_shared_private_ip", RAFT)
        self.assertIn("vault_shared_join_target", RAFT)
        self.assertIn("vault_raft_retry_join", RAFT)
        self.assertNotIn("vault operator init", ENTRYPOINT + RAFT)
        self.assertNotIn("vault operator unseal", ENTRYPOINT + RAFT)
        self.assertNotIn("VAULT_SERVER_ROOT_ACCESS_TOKEN", ENTRYPOINT + RAFT)

    def test_raft_explicitly_disables_mlock_only_without_swap(self):
        self.assertIn("disable_mlock = true", VAULT_TASKS)
        self.assertEqual(RAFT.count("ansible_swaptotal_mb | int == 0"), 2)


if __name__ == "__main__":
    unittest.main()
