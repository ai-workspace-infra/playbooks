import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ingest", Path(__file__).with_name("render_ingest_sql.py"))
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class IngestTests(unittest.TestCase):
    def test_sql_literals_and_nul(self):
        self.assertEqual(ingest.literal("name'; DROP TABLE x;--"), "'name''; DROP TABLE x;--'")
        with self.assertRaises(ValueError):
            ingest.literal("bad\x00")

    def test_failure_receipt_is_transactional_and_does_not_delete(self):
        document = {
            "schema_version": "cmdb.observations.v1",
            "run": {
                "run_id": "run-a",
                "collector": "gcp-compute",
                "owner_sha": "a" * 40,
                "scope": "shared",
                "started_at": "2026-10-09T00:00:00Z",
                "completed_at": "2026-10-09T00:01:00Z",
                "outcome": "failed",
                "scope_complete": False,
                "error_class": "http_403",
            },
            "observations": [],
        }
        sql = ingest.render(document)
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("IS DISTINCT FROM", sql)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("cmdb.resources", sql)

    def test_vm_k3s_and_serverless_ingestion_with_dedup_and_complete_scope_deletion(self):
        vm_obs_t1 = {
            "provider": "gcp",
            "account_ref": "open-platform-prod",
            "project_ref": "open-platform-prod",
            "resource_kind": "compute",
            "native_resource_id": "projects/open-platform-prod/zones/asia-east1-a/instances/101",
            "region": "asia-east1",
            "scope": "prod",
            "name": "web-saas-prod",
            "provider_state": "running",
            "provider_state_raw": "RUNNING",
            "public_endpoint": "198.51.100.10",
            "private_endpoint": "10.70.0.10",
            "source": "provider_api",
            "observed_at": "2026-10-25T10:00:01Z",
            "attributes": {"zone": "asia-east1-a"},
        }
        vm_obs_t2_replay = {
            **vm_obs_t1,
            "observed_at": "2026-10-25T10:00:03Z",
        }
        k3s_obs = {
            "provider": "akamai-cloud",
            "account_ref": "svc.plus",
            "project_ref": "svc.plus",
            "resource_kind": "k3s_node",
            "native_resource_id": "linode-9001",
            "region": "jp-tyo-3",
            "scope": "prod",
            "name": "web-saas-k3s-v5",
            "provider_state": "running",
            "provider_state_raw": "running",
            "public_endpoint": "198.51.100.20",
            "private_endpoint": "10.0.0.20",
            "source": "provider_api",
            "observed_at": "2026-10-25T10:00:02Z",
            "attributes": {"deployment_mode": "kubernetes", "k8s_cluster_name": "web-saas-k3s-v5"},
        }
        document = {
            "schema_version": "cmdb.observations.v1",
            "run": {
                "run_id": "run-complete-1",
                "collector": "gcp-compute",
                "owner_sha": "a" * 40,
                "scope": "prod",
                "account_ref": "open-platform-prod",
                "project_ref": "open-platform-prod",
                "region_ref": "all-zones",
                "resource_kind": "compute",
                "started_at": "2026-10-25T10:00:00Z",
                "completed_at": "2026-10-25T10:00:05Z",
                "outcome": "success",
                "scope_complete": True,
            },
            # Replay vm_obs twice to verify idempotent deduplication in SQL generation
            "observations": [vm_obs_t1, vm_obs_t2_replay],
        }
        sql = ingest.render(document)
        self.assertEqual(sql.count("INSERT INTO cmdb.resources "), 1)
        self.assertIn("gcp:open-platform-prod:asia-east1:projects/open-platform-prod/zones/asia-east1-a/instances/101", sql)
        self.assertIn("2026-10-25T10:00:03Z", sql)
        # Complete scope run must transition unseen prior resources in the scope to deletion_confirmed
        self.assertIn("SET lifecycle='deletion_confirmed'", sql)
        self.assertIn("canonical_id NOT IN", sql)

        # Verify K3s node runtime_form classification
        k3s_norm = ingest._normalize_observation(k3s_obs, {"scope": "prod", "outcome": "success"})
        self.assertEqual(k3s_norm["runtime_form"], "k3s_node")
        self.assertEqual(k3s_norm["resource_key"], "akamai:svc.plus:jp-tyo-3:linode-9001")

    def test_serverless_allows_null_ssh_and_ip_and_partial_run_never_deletes(self):
        serverless_obs = {
            "provider": "gcp",
            "account_ref": "open-platform-prod",
            "project_ref": "open-platform-prod",
            "resource_kind": "serverless_service",
            "native_resource_id": "projects/open-platform-prod/locations/asia-east1/services/uid-1",
            "region": "asia-east1",
            "scope": "prod",
            "name": "prod-accounts",
            "provider_state": "ready",
            "provider_state_raw": "CONDITION_SUCCEEDED",
            "public_endpoint": "https://prod-accounts.a.run.app",
            "private_endpoint": None,
            "ssh_host": None,
            "primary_ip": None,
            "source": "provider_api",
            "observed_at": "2026-10-25T10:05:01Z",
            "attributes": {"service_name": "prod-accounts"},
        }
        partial_doc = {
            "schema_version": "cmdb.observations.v1",
            "run": {
                "run_id": "run-partial-1",
                "collector": "gcp-cloudrun",
                "owner_sha": "b" * 40,
                "scope": "prod",
                "account_ref": "open-platform-prod",
                "project_ref": "open-platform-prod",
                "region_ref": "asia-east1",
                "resource_kind": "serverless_service",
                "started_at": "2026-10-25T10:05:00Z",
                "completed_at": "2026-10-25T10:05:05Z",
                "outcome": "partial",
                "scope_complete": False,
                "error_class": "incomplete_region",
            },
            "observations": [serverless_obs],
        }
        sql = ingest.render(partial_doc)
        self.assertIn("INSERT INTO cmdb.resources ", sql)
        self.assertIn('"runtime_form":"serverless"', sql)
        self.assertIn('"ssh_host":null', sql)
        self.assertIn('"primary_ip":null', sql)
        self.assertIn('"observation_status":"collection_failed"', sql)
        # Partial run must NEVER mark unseen assets in the scope as deletion_confirmed
        self.assertNotIn("deletion_confirmed", sql)

        # Serverless with fabricated ssh_host or primary_ip must be rejected
        with self.assertRaisesRegex(ValueError, "serverless observation cannot define ssh_host"):
            ingest.render(
                {
                    **partial_doc,
                    "observations": [{**serverless_obs, "ssh_host": "198.51.100.99"}],
                }
            )

        # Cross-environment mismatch (run=prod, obs=uat) must be rejected
        with self.assertRaisesRegex(ValueError, "conflicts with run scope"):
            ingest.render(
                {
                    **partial_doc,
                    "observations": [{**serverless_obs, "scope": "uat"}],
                }
            )


if __name__ == "__main__":
    unittest.main()
