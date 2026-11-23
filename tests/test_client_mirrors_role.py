"""Contract and offline API unit tests for roles/saas/client_mirrors."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE_FILES = ROOT / "roles" / "saas" / "client_mirrors" / "files"
sys.path.insert(0, str(ROLE_FILES))
import client_mirrors_reconcile as rec  # noqa: E402


class _FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


SAMPLE_MANIFEST = """---
apiVersion: gitops.svc.plus/v1alpha1
kind: ClientMirrorStack
metadata:
  name: svc-plus-client-mirrors
  project: svc.plus
  environment: prod
spec:
  global:
    zone_name: svc.plus
    managed_by: playbooks-roles-saas-client_mirrors
  products:
    - slug: xconnect
      prefixes: [releases/xconnect/]
    - slug: xworkmate
      prefixes: [releases/xworkmate/]
  admin_vault_sources:
    cloudflare: kv/prod/serverless/cloudflare
    alicloud: kv/prod/platform/alicloud
  cloudflare_r2:
    bucket_name: svc-plus-releases-global
    custom_domain: dl.svc.plus
    location: apac
    min_tls: "1.2"
    cors_allowed_origins: [https://console.svc.plus, https://xworktech.com]
    cors_allowed_methods: [GET, HEAD]
    cors_max_age_seconds: 3600
    token_name: svc-plus-client-mirrors-r2-rw
    vault_kv_path: prod/platform/storage/cloudflare-r2
  aliyun_oss_cdn:
    bucket_name: svc-plus-releases-cn-hk
    oss_region: cn-hongkong
    oss_acl: public-read
    cdn_domain: cn-dl.svc.plus
    cdn_type: download
    cdn_scope: overseas
    cdn_rate_limit: 10m
    cdn_rate_limit_after: 1m
    cdn_cas_cert_id: ""
    cors_allowed_origins: [https://console.svc.plus, https://xworktech.com]
    cors_allowed_methods: [GET, HEAD]
    cors_max_age_seconds: 3600
    ram_user_name: ci-client-mirrors-oss-rw
    ram_policy_name: ci-client-mirrors-oss-rw-policy
    vault_kv_path: prod/platform/storage/aliyun-oss
"""


class ClientMirrorsRoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="client-mirrors-role-test-"))
        self.manifest = self.tmp / "mirrors.yaml"
        self.manifest.write_text(SAMPLE_MANIFEST, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_role_lives_in_saas_namespace_and_manifest_rejects_secrets(self):
        role_dir = ROOT / "roles" / "saas" / "client_mirrors"
        self.assertTrue((role_dir / "README.md").is_file())
        self.assertTrue((role_dir / "defaults" / "main.yml").is_file())
        self.assertTrue((role_dir / "tasks" / "main.yml").is_file())

        bad_manifest = self.tmp / "bad.yaml"
        bad_manifest.write_text(SAMPLE_MANIFEST + "\n  extra:\n    api_token: leak\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            rec.load_and_validate_manifest(bad_manifest)

    def test_plan_mode_performs_zero_mutations(self):
        mutations = []

        def fake_opener(req, timeout=25):
            method = req.get_method()
            url = req.full_url
            if method not in ("GET", "HEAD"):
                mutations.append((method, url))
            if "/v1/kv/data/prod/serverless/cloudflare" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"data": {"data": {"CLOUDFLARE_API_TOKEN": "cf-tok", "CLOUDFLARE_ACCOUNT_ID": "acct-1"}}}).encode())
            if "/v1/kv/data/prod/platform/alicloud" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"data": {"data": {"access_key_id": "ali-ak", "access_key_secret": "ali-sk"}}}).encode())
            if "/v1/kv/data/prod/platform/storage/" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/zones?name=svc.plus" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "zone-1"}]}).encode())
            if "/r2/buckets/" in url or "oss-cn-hongkong" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "DescribeVerifyContent" in url:
                return _FakeResponse(body=json.dumps({"Content": "verify-tok"}).encode())
            if "/dns_records" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": []}).encode())
            if "DescribeCdnDomainDetail" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            raise AssertionError(f"Unexpected call in plan: {method} {url}")

        res = rec.reconcile(
            {
                "manifest_path": str(self.manifest),
                "mode": "plan",
                "apply": False,
                "verify": False,
                "vault_addr": "https://vault.example",
                "vault_token": "mock-vault-token",
            },
            opener=fake_opener,
            wait_dns_seconds=0,
        )
        self.assertEqual(mutations, [])
        self.assertFalse(res["changed"])
        self.assertIn("r2_bucket:would-create", res["cloudflare_r2"]["actions"])
        self.assertIn("oss_bucket:would-create", res["aliyun_oss_cdn"]["actions"])
        self.assertIn("cdn_domain:would-create", res["aliyun_oss_cdn"]["actions"])

    def test_apply_mode_reconciles_writes_vault_and_probes_without_leaking_secrets(self):
        vault_writes = {}
        calls = []
        created_cf_secret = "cf-raw-secret-token-value"
        created_ali_secret = "ali-ram-secret-key-value"

        def fake_opener(req, timeout=25):
            method = req.get_method()
            url = req.full_url
            calls.append((method, url))
            if "/v1/kv/data/prod/serverless/cloudflare" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"CLOUDFLARE_API_TOKEN": "cf-adm", "CLOUDFLARE_ACCOUNT_ID": "acct-1"}}}).encode())
            if "/v1/kv/data/prod/platform/alicloud" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"access_key_id": "ali-adm-ak", "access_key_secret": "ali-adm-sk"}}}).encode())
            if "/v1/kv/data/prod/platform/storage/" in url:
                if method == "POST":
                    path = url.split("/v1/kv/data/", 1)[1]
                    vault_writes[path] = json.loads(req.data.decode("utf-8"))["data"]
                    return _FakeResponse(body=b"{}")
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/zones?name=svc.plus" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "zone-1"}]}).encode())
            if url.endswith("/r2/buckets/svc-plus-releases-global") and method == "GET":
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if url.endswith("/r2/buckets") and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"name": "svc-plus-releases-global"}}).encode())
            if "/domains/custom" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"domains": []}}).encode())
            if "/domains/custom" in url and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"domain": "dl.svc.plus"}}).encode())
            if "/cors" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"rules": []}}).encode())
            if "/cors" in url and method == "PUT":
                return _FakeResponse(body=json.dumps({"success": True, "result": {}}).encode())
            if "/tokens/permission_groups" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "pg-r2-w", "name": "Workers R2 Storage Bucket Item Write"}]}).encode())
            if url.endswith("/tokens") and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "r2-tok-id", "value": created_cf_secret}}).encode())
            if "oss-cn-hongkong" in url and method == "HEAD":
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "oss-cn-hongkong" in url and method == "PUT":
                return _FakeResponse(status=200, body=b"")
            if "DescribeVerifyContent" in url:
                return _FakeResponse(body=json.dumps({"Content": "verify-tok"}).encode())
            if "VerifyDomainOwner" in url:
                return _FakeResponse(body=json.dumps({"RequestId": "req-1"}).encode())
            if "/dns_records" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": []}).encode())
            if "/dns_records" in url and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "rec-1"}}).encode())
            if "DescribeCdnDomainDetail" in url:
                return _FakeResponse(body=json.dumps({"GetDomainDetailModel": {"Cname": "cn-dl.svc.plus.w.alikunlun.com"}}).encode())
            if "BatchSetCdnDomainConfig" in url:
                return _FakeResponse(body=json.dumps({"DomainConfigList": []}).encode())
            if "GetUser" in url or "GetPolicy" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if any(act in url for act in ("CreateUser", "CreatePolicy", "AttachPolicyToUser")):
                return _FakeResponse(body=b"{}")
            if "ListPoliciesForUser" in url:
                return _FakeResponse(body=json.dumps({"Policies": {"Policy": []}}).encode())
            if "CreateAccessKey" in url:
                return _FakeResponse(body=json.dumps({"AccessKey": {"AccessKeyId": "ram-ak-1", "AccessKeySecret": created_ali_secret}}).encode())
            if "r2.cloudflarestorage.com" in url and method == "PUT":
                return _FakeResponse(status=200, body=b"")
            if url in ("https://dl.svc.plus/releases/_health/probe.json", "https://cn-dl.svc.plus/releases/_health/probe.json"):
                return _FakeResponse(status=200, body=b'{"status":"ok"}', headers={"Access-Control-Allow-Origin": "https://console.svc.plus"})
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")

        res = rec.reconcile(
            {
                "manifest_path": str(self.manifest),
                "mode": "apply",
                "apply": True,
                "verify": True,
                "vault_addr": "https://vault.example",
                "vault_token": "mock-vault-token",
            },
            opener=fake_opener,
            wait_dns_seconds=0,
        )
        self.assertTrue(res["changed"])
        serialized = json.dumps(res)
        self.assertNotIn(created_cf_secret, serialized)
        self.assertNotIn(created_ali_secret, serialized)
        self.assertNotIn(hashlib.sha256(created_cf_secret.encode()).hexdigest(), serialized)
        self.assertEqual(
            vault_writes["prod/platform/storage/cloudflare-r2"]["secret_access_key"],
            hashlib.sha256(created_cf_secret.encode()).hexdigest(),
        )
        self.assertEqual(vault_writes["prod/platform/storage/aliyun-oss"]["access_key_secret"], created_ali_secret)
        self.assertTrue(all(p["ok"] for p in res["verification"]["https_probes"]))

        # Verify idempotent second run preserves minted keys without re-issuing tokens/AKs.
        second_calls = []

        def second_opener(req, timeout=25):
            second_calls.append((req.get_method(), req.full_url))
            if "/v1/kv/data/prod/platform/storage/" in req.full_url and req.get_method() == "GET":
                path = req.full_url.split("/v1/kv/data/", 1)[1]
                return _FakeResponse(body=json.dumps({"data": {"data": vault_writes[path]}}).encode())
            if "ListPoliciesForUser" in req.full_url:
                return _FakeResponse(body=json.dumps({"Policies": {"Policy": [{"PolicyName": "ci-client-mirrors-oss-rw-policy"}]}}).encode())
            return fake_opener(req, timeout=timeout)

        res2 = rec.reconcile(
            {
                "manifest_path": str(self.manifest),
                "mode": "apply",
                "apply": True,
                "verify": False,
                "vault_addr": "https://vault.example",
                "vault_token": "mock-vault-token",
            },
            opener=second_opener,
            wait_dns_seconds=0,
        )
        self.assertIn("r2_token:preserved", res2["cloudflare_r2"]["actions"])
        self.assertIn("ram_ak:preserved", res2["aliyun_oss_cdn"]["actions"])
        self.assertFalse(any("CreateAccessKey" in url or url.endswith("/tokens") for _, url in second_calls))

    def test_shell_and_ansible_playbook_syntax(self):
        script = ROLE_FILES / "bootstrap-admin-vault-kv.sh"
        bash_res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(bash_res.returncode, 0, bash_res.stderr)

        ap = shutil.which("ansible-playbook")
        self.assertIsNotNone(ap)
        pb_res = subprocess.run(
            [ap, "--syntax-check", "-i", "localhost,", str(ROOT / "configure_client_mirrors.yml")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(pb_res.returncode, 0, pb_res.stderr)


if __name__ == "__main__":
    unittest.main()
