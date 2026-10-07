import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "node_deploy" / "render_inventory.py"
SPEC = importlib.util.spec_from_file_location("node_inventory", SCRIPT)
node_inventory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(node_inventory)


def contract():
    return {
        "apiVersion": "ops.svc.plus/v1alpha1",
        "kind": "NodeDeployment",
        "metadata": {"name": "vault-shared"},
        "spec": {
            "environment": "shared",
            "stages": ["vault-shared-leader", "node-process-metrics"],
            "stage_targets": {
                "vault-shared-leader": ["vault_shared_leader"],
                "node-process-metrics": ["vault_shared_nodes"],
            },
            "nodes": [
                {
                    "id": "vault-prod-0",
                    "provider": "gcp",
                    "address": "203.0.113.10",
                    "private_address": "10.81.0.4",
                    "ssh_user": "gha_1234567890",
                    "auth": {"adapter": "gcp-oslogin-ephemeral"},
                    "groups": ["vault_shared_nodes", "vault_shared_leader"],
                },
                {
                    "id": "vault-prod-1",
                    "provider": "vps-provider",
                    "address": "vault-1.example.net",
                    "ssh_port": 2222,
                    "ssh_user": "ops",
                    "auth": {"adapter": "ssh-certificate"},
                },
            ],
        },
    }


class NodeDeploymentContractTests(unittest.TestCase):
    def test_accepts_gcp_and_vps_with_the_same_contract(self):
        doc = node_inventory.validate(contract())
        rendered = node_inventory.render(doc)
        self.assertIn("vault-prod-0 ansible_host=203.0.113.10", rendered)
        self.assertIn("vault_shared_private_ip=10.81.0.4", rendered)
        self.assertIn("vault-prod-1 ansible_host=vault-1.example.net ansible_user=ops ansible_port=2222", rendered)
        self.assertIn("node_auth_adapter=ssh-certificate", rendered)

    def test_connection_mode_is_optional_but_constrained(self):
        doc = contract()
        doc["spec"]["connection"] = {"mode": "xconnect-zero"}
        node_inventory.validate(doc)
        doc["spec"]["connection"] = {"mode": "public-forever"}
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)

    def test_rejects_embedded_credentials(self):
        doc = contract()
        doc["spec"]["nodes"][0]["private_key"] = "DO NOT STORE"
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)

    def test_rejects_unknown_stage_or_adapter(self):
        doc = contract()
        doc["spec"]["stages"].append("arbitrary-shell")
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)
        doc = contract()
        doc["spec"]["nodes"][0]["auth"]["adapter"] = "static-private-key"
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)

    def test_rejects_duplicate_node_ids_and_invalid_address(self):
        doc = contract()
        doc["spec"]["nodes"][1]["id"] = "vault-prod-0"
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)

    def test_rejects_stage_targets_that_are_missing_or_unknown(self):
        doc = contract()
        doc["spec"]["stage_targets"].pop("vault-shared-leader")
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)

    def test_rejects_public_raft_address(self):
        doc = contract()
        doc["spec"]["nodes"][0]["private_address"] = "8.8.8.8"
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)
        doc = contract()
        doc["spec"]["stage_targets"]["vault-shared-leader"] = ["not-a-group"]
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)
        doc = contract()
        doc["spec"]["nodes"][0]["address"] = "--bad host--"
        with self.assertRaises(SystemExit):
            node_inventory.validate(doc)


if __name__ == "__main__":
    unittest.main()
