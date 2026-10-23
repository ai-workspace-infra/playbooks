from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SnapshotTokenIdempotencyTest(unittest.TestCase):
    def test_all_snapshot_entrypoints_use_the_public_resolver(self):
        for name in (
            "deploy_xray_exporter.yml",
            "deploy_observability_agent.yml",
            "deploy_xray_proxy_server.yml",
        ):
            content = (ROOT / name).read_text()
            self.assertIn("tasks/resolve-canonical-service-token.yml", content, name)
            self.assertNotIn("lookup('ansible.builtin.env', 'INTERNAL_SERVICE_TOKEN')", content, name)

    def test_resolver_is_fixed_to_canonical_vault_key(self):
        content = (ROOT / "tasks/resolve-canonical-service-token.yml").read_text()
        self.assertIn("kv/prod/accounts/runtime", content)
        self.assertIn("-field=INTERNAL_SERVICE_TOKEN", content)
        self.assertIn("no_log: true", content)

    def test_existing_snapshot_config_is_protected_before_template(self):
        tasks = (ROOT / "roles/vhosts/xray-exporter/tasks/main.yml").read_text()
        protection = tasks.index("protect-existing-snapshots.yml")
        render = tasks.index("Render xray-exporter runtime environment")
        self.assertLess(protection, render)
        protection_tasks = (ROOT / "roles/vhosts/xray-exporter/tasks/protect-existing-snapshots.yml").read_text()
        self.assertIn("refusing to overwrite", protection_tasks)
        self.assertIn("search('(?m)^VECTOR_SNAPSHOT_URL=\"?http')", protection_tasks)

    def test_resolved_token_is_bound_to_each_consumer_contract(self):
        observability = (ROOT / "deploy_observability_agent.yml").read_text()
        proxy = (ROOT / "deploy_xray_proxy_server.yml").read_text()
        self.assertIn("vector_billing_ingest_token: \"{{ canonical_internal_service_token }}\"", observability)
        self.assertIn("agent_api_token: \"{{ canonical_internal_service_token }}\"", proxy)

    def test_only_exporter_handler_is_used_for_runtime_environment_changes(self):
        tasks = (ROOT / "roles/vhosts/xray-exporter/tasks/main.yml").read_text()
        render = tasks[tasks.index("Render xray-exporter runtime environment"):]
        self.assertIn("notify: Restart xray-exporters", render)
        self.assertNotIn("notify: Restart Xray services", render)


if __name__ == "__main__":
    unittest.main()
