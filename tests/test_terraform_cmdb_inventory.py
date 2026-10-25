import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "inventory" / "terraform_cmdb.py"
SPEC = importlib.util.spec_from_file_location("terraform_cmdb", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class TerraformCmdbInventoryTest(unittest.TestCase):
    def test_cmdb_v1_uses_hosts_and_preserves_provider_facts(self):
        cmdb = {
            "schema_version": "cmdb.v1",
            "environment": "prod",
            "cloud_provider": "gcp-cloud",
            "project_id": "open-platform-prod",
            "source_resources": ["resources/svc.plus/prod/gcp/web-saas.yaml"],
            "hosts": {
                "web-saas-prod": {
                    "ip": "198.51.100.10",
                    "resource_id": "projects/open-platform-prod/zones/asia-east1-a/instances/web-saas-prod",
                    "ansible_user": "oslogin_user",
                    "groups": ["web_saas"],
                    "host_vars": {"service_domains": "console-selfhost-prod.svc.plus"},
                }
            },
        }

        inventory = MODULE.build_inventory(cmdb)

        self.assertEqual(inventory["web_saas"]["hosts"], ["web-saas-prod"])
        hostvars = inventory["_meta"]["hostvars"]["web-saas-prod"]
        self.assertEqual(hostvars["ansible_host"], "198.51.100.10")
        self.assertEqual(hostvars["cmdb_cloud_provider"], "gcp-cloud")
        self.assertEqual(hostvars["cmdb_project_id"], "open-platform-prod")
        self.assertEqual(hostvars["cmdb_resource_id"].split("/")[-1], "web-saas-prod")
        self.assertEqual(hostvars["cmdb_region"], "asia-east1")
        self.assertEqual(hostvars["region"], "asia-east1")
        self.assertEqual(hostvars["runtime_form"], "systemd")
        self.assertEqual(
            hostvars["resource_key"],
            "gcp-cloud:open-platform-prod:asia-east1:projects/open-platform-prod/zones/asia-east1-a/instances/web-saas-prod",
        )
        self.assertEqual(hostvars["service_domains"], "console-selfhost-prod.svc.plus")

    def test_serverless_records_are_excluded_from_ansible_ssh_inventory(self):
        cmdb = {
            "schema_version": "cmdb.v1",
            "environment": "prod",
            "cloud_provider": "gcp",
            "project_id": "open-platform-prod",
            "hosts": {
                "web-saas-k3s-v5": {
                    "ip": "104.105.141.213",
                    "resource_id": "linode:ap-west:web-saas-k3s-v5",
                    "region": "ap-west",
                    "groups": ["k3s_cluster", "obs_k3s_node"],
                },
                "docker-compose-edge-jp": {
                    "ip": "198.51.100.44",
                    "resource_id": "aws:ap-northeast-1:i-0abc12345",
                    "region": "ap-northeast-1",
                    "groups": ["docker_hosts", "proxy"],
                },
                "cloudrun-accounts-svc": {
                    "resource_id": "projects/open-platform-prod/locations/asia-northeast1/services/accounts",
                    "resource_type": "cloud_run_service",
                    "runtime_form": "serverless",
                    "groups": ["serverless"],
                },
                "cloudflare-worker-edge": {
                    "ip": "192.0.2.1",
                    "resource_id": "cf:worker:edge-router",
                    "resource_type": "cloudflare_worker",
                    "groups": ["workers"],
                },
                "supabase-db-serverless": {
                    "ip": "192.0.2.2",
                    "resource_id": "supabase:ap-northeast-1:db-main",
                    "ssh_enabled": False,
                    "groups": ["database"],
                },
            },
        }

        inventory = MODULE.build_inventory(cmdb)
        hostvars = inventory["_meta"]["hostvars"]

        self.assertIn("web-saas-k3s-v5", hostvars)
        self.assertIn("docker-compose-edge-jp", hostvars)
        self.assertNotIn("cloudrun-accounts-svc", hostvars)
        self.assertNotIn("cloudflare-worker-edge", hostvars)
        self.assertNotIn("supabase-db-serverless", hostvars)
        self.assertNotIn("serverless", inventory)
        self.assertNotIn("workers", inventory)

        k3s_vars = hostvars["web-saas-k3s-v5"]
        self.assertEqual(k3s_vars["runtime_form"], "k3s")
        self.assertEqual(k3s_vars["region"], "ap-west")
        self.assertEqual(
            k3s_vars["resource_key"],
            "gcp:open-platform-prod:ap-west:linode:ap-west:web-saas-k3s-v5",
        )

        docker_vars = hostvars["docker-compose-edge-jp"]
        self.assertEqual(docker_vars["runtime_form"], "docker")
        self.assertEqual(docker_vars["region"], "ap-northeast-1")

    def test_legacy_metadata_is_not_treated_as_a_host(self):
        cmdb = {
            "environment": "uat",
            "project_id": "open-platform-uat",
            "web-saas-uat": {
                "ip": "198.51.100.20",
                "groups": ["web_saas"],
            },
        }

        inventory = MODULE.build_inventory(cmdb)

        self.assertNotIn("environment", inventory["_meta"]["hostvars"])
        self.assertEqual(inventory["web_saas"]["hosts"], ["web-saas-uat"])

    def test_cmdb_file_environment_is_consumed(self):
        cmdb = {
            "schema_version": "cmdb.v1",
            "environment": "prod",
            "hosts": {
                "web-saas-prod": {"ip": "198.51.100.30", "groups": ["web_saas"]}
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cmdb.json"
            path.write_text(json.dumps(cmdb), encoding="utf-8")
            with patch.dict(os.environ, {"CMDB_FILE": str(path)}, clear=False):
                loaded = MODULE.load_cmdb()
        self.assertEqual(loaded["schema_version"], "cmdb.v1")


if __name__ == "__main__":
    unittest.main()
