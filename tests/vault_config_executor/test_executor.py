import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "executor", ROOT / "roles/vhosts/vault_config_executor/files/executor.py")
ex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ex)


class FakeVault:
    def __init__(self, state=None):
        self.state = state or {}
        self.writes = []

    def read(self, path):
        return self.state.get(path)

    def call(self, method, path, payload=None, missing_ok=False):
        self.writes.append((method, path, payload))
        if path.endswith("/config/ca"):
            self.state[path] = {"public_key": "ssh-ed25519 AAAAfake"}
        if "/data/" in path:
            cas = payload["options"]["cas"]
            cur = self.state.get(path)
            if (cur or {}).get("metadata", {}).get("version", 0) != cas:
                raise RuntimeError("cas mismatch")
            self.state[path] = {"data": payload["data"], "metadata": {"version": cas + 1}}
        return {}


def kv(**over):
    req = {"operation": "kv_fields", "environment": "uat", "path": "uat/accounts/runtime",
           "fields": {"BRIDGE_CREDENTIAL_TOKEN_PEPPER": "x" * 40}}
    req.update(over)
    return req


class KvFields(unittest.TestCase):
    def test_preview_never_writes(self):
        v = FakeVault()
        r = ex.run(kv(), v)
        self.assertEqual(v.writes, [])
        self.assertEqual(r["pending"], ["BRIDGE_CREDENTIAL_TOKEN_PEPPER"])
        self.assertNotIn("x" * 40, json.dumps(r))

    def test_apply_needs_confirmation(self):
        with self.assertRaises(ValueError):
            ex.run(kv(apply=True), FakeVault())

    def test_apply_creates_with_cas_zero_and_preserves_other_fields(self):
        path = "kv/data/uat/accounts/runtime"
        v = FakeVault({path: {"data": {"OTHER": "keep"}, "metadata": {"version": 3}}})
        ex.run(kv(apply=True, confirm="APPLY-VAULT-KV_FIELDS-UAT"), v)
        self.assertEqual(v.writes[0][2]["options"]["cas"], 3)
        self.assertEqual(v.state[path]["data"]["OTHER"], "keep")

    def test_existing_differing_value_preserved_without_overwrite(self):
        path = "kv/data/uat/accounts/runtime"
        v = FakeVault({path: {"data": {"BRIDGE_CREDENTIAL_TOKEN_PEPPER": "old"}, "metadata": {"version": 1}}})
        r = ex.run(kv(apply=True, confirm="APPLY-VAULT-KV_FIELDS-UAT"), v)
        self.assertEqual(v.writes, [])
        self.assertEqual(r["preserved_existing"], ["BRIDGE_CREDENTIAL_TOKEN_PEPPER"])

    def test_cross_environment_path_rejected(self):
        with self.assertRaises(ValueError):
            ex.run(kv(path="prod/accounts/runtime"), FakeVault())


class SshCa(unittest.TestCase):
    req = {"operation": "ssh_ca", "environment": "prod", "role": "vault-legacy-ops", "principal": "vault-migrate"}

    def test_apply_creates_mount_ca_role(self):
        v = FakeVault({"sys/mounts": {}})
        r = ex.run(dict(self.req, apply=True, confirm="APPLY-VAULT-SSH_CA-PROD"), v)
        self.assertEqual(r["pending"], ["mount", "ca", "role"])
        self.assertTrue(r["ca_public_key"].startswith("ssh-ed25519"))

    def test_existing_ca_never_regenerated(self):
        role = {"key_type": "ca", "allow_user_certificates": True, "allowed_users": "vault-migrate",
                "default_user": "vault-migrate", "allowed_extensions": "", "ttl": 1800, "max_ttl": 1800,
                "algorithm_signer": "default"}
        v = FakeVault({"sys/mounts": {"ssh-client-signer/": {}},
                       "ssh-client-signer/config/ca": {"public_key": "ssh-ed25519 AAAAold"},
                       "ssh-client-signer/roles/vault-legacy-ops": role})
        r = ex.run(dict(self.req, apply=True, confirm="APPLY-VAULT-SSH_CA-PROD"), v)
        self.assertEqual(r["pending"], [])
        self.assertEqual(v.writes, [])


class JwtContract(unittest.TestCase):
    contract = {"role_name": "uat-compute-cleanup",
                "role": {"bound_claims_type": "string", "token_policies": ["uat-compute-cleanup"],
                         "bound_claims": {"ref": "refs/heads/main",
                                          "job_workflow_ref": "o/r/.github/workflows/c.yml@refs/heads/main"}},
                "policy": {"path": {"kv/data/uat/serverless/gcp": {"capabilities": ["read"]}}}}

    def test_preview_lists_policy_and_role(self):
        r = ex.run({"operation": "jwt_contract", "environment": "uat", "contract": self.contract}, FakeVault())
        self.assertEqual(r["pending"], ["policy", "role"])

    def test_write_capability_rejected_by_default(self):
        c = json.loads(json.dumps(self.contract))
        c["policy"]["path"]["kv/data/uat/serverless/gcp"]["capabilities"] = ["update"]
        with self.assertRaises(ValueError):
            ex.run({"operation": "jwt_contract", "environment": "uat", "contract": c}, FakeVault())

    def test_wildcard_workflow_rejected(self):
        c = json.loads(json.dumps(self.contract))
        c["role"]["bound_claims"]["job_workflow_ref"] = "o/r/.github/workflows/*@refs/heads/main"
        with self.assertRaises(ValueError):
            ex.run({"operation": "jwt_contract", "environment": "uat", "contract": c}, FakeVault())


if __name__ == "__main__":
    unittest.main()
