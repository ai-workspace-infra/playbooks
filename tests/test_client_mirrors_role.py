"""Contract and offline API unit tests for roles/saas/client_mirrors."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
            if "oss-cn-hongkong" in url and method == "DELETE":
                return _FakeResponse(status=204, body=b"")
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
        del_idx = calls.index(("DELETE", "https://svc-plus-releases-cn-hk.oss-cn-hongkong.aliyuncs.com/?publicAccessBlock"))
        acl_idx = calls.index(("PUT", "https://svc-plus-releases-cn-hk.oss-cn-hongkong.aliyuncs.com/?acl"))
        self.assertLess(del_idx, acl_idx)
        create_policy_urls = [url for _, url in calls if "Action=CreatePolicy&" in url]
        self.assertEqual(len(create_policy_urls), 1)
        decoded_policy = rec.urllib.parse.unquote(create_policy_urls[0])
        self.assertIn("oss:GetObjectAcl", decoded_policy)
        self.assertIn("oss:GetBucketLocation", decoded_policy)

        # Verify idempotent second run preserves minted keys without re-issuing tokens/AKs.
        second_calls = []

        def second_opener(req, timeout=25):
            second_calls.append((req.get_method(), req.full_url))
            if "/v1/kv/data/prod/platform/storage/" in req.full_url and req.get_method() == "GET":
                path = req.full_url.split("/v1/kv/data/", 1)[1]
                return _FakeResponse(body=json.dumps({"data": {"data": vault_writes[path]}}).encode())
            if any(act in req.full_url for act in ("GetUser", "GetPolicy", "CreatePolicyVersion")):
                return _FakeResponse(body=b"{}")
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
        self.assertIn("ram_policy:updated", res2["aliyun_oss_cdn"]["actions"])
        policy_ver_urls = [url for _, url in second_calls if "Action=CreatePolicyVersion" in url]
        self.assertEqual(len(policy_ver_urls), 1)
        self.assertIn("RotateStrategy=DeleteOldestNonDefaultVersionWhenLimitExceeded", policy_ver_urls[0])
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

    def test_bootstrap_script_non_interactive_alicloud(self):
        script = ROLE_FILES / "bootstrap-admin-vault-kv.sh"
        capture = self.tmp / "vault_capture.txt"
        fake_vault = self.tmp / "fake-vault"
        fake_vault.write_text(
            f"""#!/usr/bin/env python3
import sys
from pathlib import Path
Path({str(capture)!r}).write_text(" ".join(sys.argv[1:]) + "\\n" + sys.stdin.read())
""",
            encoding="utf-8",
        )
        fake_vault.chmod(fake_vault.stat().st_mode | stat.S_IXUSR)

        env = dict(os.environ)
        env.update(
            {
                "VAULT_ADDR": "https://vault.example",
                "VAULT_BIN": str(fake_vault),
                "ALICLOUD_ACCESS_KEY": "mock-ali-ak-id",
                "ALICLOUD_SECRET_KEY": "mock-ali-secret-val\r",
            }
        )
        proc = subprocess.run(
            ["bash", str(script), "--provider", "alicloud", "--non-interactive"],
            stdin=subprocess.DEVNULL,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("mock-ali-secret-val", proc.stdout)
        self.assertNotIn("mock-ali-secret-val", proc.stderr)
        recorded = capture.read_text(encoding="utf-8")
        cmd_line, body_line = recorded.splitlines()
        self.assertEqual(cmd_line, "kv put -mount=kv -cas=0 prod/platform/alicloud -")
        self.assertEqual(
            json.loads(body_line),
            {"access_key_id": "mock-ali-ak-id", "access_key_secret": "mock-ali-secret-val"},
        )

    def test_main_emits_sanitized_json_error_on_failure(self):
        script = ROLE_FILES / "client_mirrors_reconcile.py"
        secret_tok = "super-secret-cf-token-val"
        payload = {
            "manifest_path": str(self.tmp / "missing-mirrors.yaml"),
            "mode": "plan",
            "apply": False,
            "verify": False,
            "vault_addr": "https://vault.example",
            "vault_token": secret_tok,
        }
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertFalse(out["ok"])
        self.assertFalse(out["changed"])
        self.assertIn("missing-mirrors.yaml", out["error"])
        self.assertNotIn(secret_tok, proc.stdout)
        self.assertNotIn(secret_tok, proc.stderr)

    def test_oss_empty_body_put_sets_explicit_content_type_matching_signature(self):
        captured = {}

        def fake_opener(req, timeout=25):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["has_content_type"] = req.has_header("Content-type")
            return _FakeResponse(status=200, body=b"")

        status, _ = rec.oss_request(
            bucket="svc-plus-releases-cn-hk",
            oss_region="cn-hongkong",
            method="PUT",
            resource_suffix="?acl",
            body=b"",
            content_type="",
            oss_headers={"x-oss-acl": "public-read"},
            ak="test-ak",
            sk="test-sk",
            opener=fake_opener,
        )
        self.assertEqual(status, 200)
        self.assertTrue(captured["has_content_type"])
        self.assertEqual(captured["headers"]["content-type"], "application/octet-stream")
        expected_sts = (
            f"PUT\n\napplication/octet-stream\n{captured['headers']['date']}\n"
            "x-oss-acl:public-read\n/svc-plus-releases-cn-hk/?acl"
        )
        expected_sig = base64.b64encode(
            hmac.new(b"test-sk", expected_sts.encode("utf-8"), hashlib.sha1).digest()
        ).decode("utf-8")
        self.assertEqual(captured["headers"]["authorization"], f"OSS test-ak:{expected_sig}")

    def test_apply_persists_r2_kv_before_aliyun_step(self):
        vault_writes = {}

        def failing_ali_opener(req, timeout=25):
            method = req.get_method()
            url = req.full_url
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
                return _FakeResponse(body=json.dumps({"success": True, "result": {"name": "svc-plus-releases-global"}}).encode())
            if "/domains/custom" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"domains": [{"domain": "dl.svc.plus", "enabled": True, "minTLS": "1.2"}]}}).encode())
            if "/cors" in url and method == "GET":
                return _FakeResponse(
                    body=json.dumps(
                        {
                            "success": True,
                            "result": {
                                "rules": [
                                    {
                                        "id": "client-mirrors-cors",
                                        "allowed": {
                                            "methods": ["GET", "HEAD"],
                                            "origins": ["https://console.svc.plus", "https://xworktech.com"],
                                            "headers": ["*"],
                                        },
                                        "exposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                                        "maxAgeSeconds": 3600,
                                    }
                                ]
                            },
                        }
                    ).encode()
                )
            if "/tokens/permission_groups" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "pg-r2-w", "name": "Workers R2 Storage Bucket Item Write"}]}).encode())
            if url.endswith("/tokens") and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "r2-tok-id", "value": "r2-raw-tok"}}).encode())
            if "oss-cn-hongkong" in url:
                raise rec.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")

        with self.assertRaises(RuntimeError):
            rec.reconcile(
                {
                    "manifest_path": str(self.manifest),
                    "mode": "apply",
                    "apply": True,
                    "verify": False,
                    "vault_addr": "https://vault.example",
                    "vault_token": "mock-vault-token",
                },
                opener=failing_ali_opener,
                wait_dns_seconds=0,
            )
        self.assertIn("prod/platform/storage/cloudflare-r2", vault_writes)
        self.assertEqual(vault_writes["prod/platform/storage/cloudflare-r2"]["access_key_id"], "r2-tok-id")

    def test_cdn_tls_vault_path_uploads_cert_via_post_and_enables_https_force(self):
        tls_manifest = self.tmp / "mirrors-tls.yaml"
        tls_manifest.write_text(
            SAMPLE_MANIFEST.replace(
                '    cdn_cas_cert_id: ""',
                '    cdn_cas_cert_id: ""\n    cdn_tls_vault_path: kv/CICD/domains/svc.plus',
            ),
            encoding="utf-8",
        )
        fake_pub = "-----BEGIN CERTIFICATE-----\nMOCKCERT\n-----END CERTIFICATE-----\n"
        fake_pri = "-----BEGIN PRIVATE KEY-----\nMOCK-PRIVATE-KEY-SECRET\n-----END PRIVATE KEY-----\n"
        captured_posts = []
        batch_urls = []

        def tls_opener(req, timeout=25):
            method = req.get_method()
            url = req.full_url
            if "/v1/kv/data/prod/serverless/cloudflare" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"CLOUDFLARE_API_TOKEN": "cf-adm", "CLOUDFLARE_ACCOUNT_ID": "acct-1"}}}).encode())
            if "/v1/kv/data/prod/platform/alicloud" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"access_key_id": "ali-adm-ak", "access_key_secret": "ali-adm-sk"}}}).encode())
            if "/v1/kv/data/CICD/domains/svc.plus" in url:
                return _FakeResponse(
                    body=json.dumps(
                        {
                            "data": {
                                "data": {
                                    "tls_fullchain_pem_b64": base64.b64encode(fake_pub.encode()).decode(),
                                    "tls_key_pem_b64": base64.b64encode(fake_pri.encode()).decode(),
                                }
                            }
                        }
                    ).encode()
                )
            if "/v1/kv/data/prod/platform/storage/" in url:
                if method == "POST":
                    return _FakeResponse(body=b"{}")
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/zones?name=svc.plus" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "zone-1"}]}).encode())
            if url.endswith("/r2/buckets/svc-plus-releases-global"):
                return _FakeResponse(body=json.dumps({"success": True, "result": {"name": "svc-plus-releases-global"}}).encode())
            if "/domains/custom" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": {"domains": [{"domain": "dl.svc.plus", "enabled": True, "minTLS": "1.2"}]}}).encode())
            if "/cors" in url:
                return _FakeResponse(
                    body=json.dumps(
                        {
                            "success": True,
                            "result": {
                                "rules": [
                                    {
                                        "id": "client-mirrors-cors",
                                        "allowed": {
                                            "methods": ["GET", "HEAD"],
                                            "origins": ["https://console.svc.plus", "https://xworktech.com"],
                                            "headers": ["*"],
                                        },
                                        "exposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                                        "maxAgeSeconds": 3600,
                                    }
                                ]
                            },
                        }
                    ).encode()
                )
            if "/tokens/permission_groups" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "pg-r2-w", "name": "Workers R2 Storage Bucket Item Write"}]}).encode())
            if url.endswith("/tokens") and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "r2-tok-id", "value": "r2-raw-tok"}}).encode())
            if "oss-cn-hongkong" in url and method == "HEAD":
                return _FakeResponse(status=200, body=b"")
            if "oss-cn-hongkong" in url and method in ("DELETE", "PUT"):
                return _FakeResponse(status=200, body=b"")
            if "DescribeVerifyContent" in url:
                return _FakeResponse(body=json.dumps({"Content": ""}).encode())
            if "DescribeCdnDomainDetail" in url:
                return _FakeResponse(body=json.dumps({"GetDomainDetailModel": {"Cname": "cn-dl.svc.plus.queniuaa.com", "ServerCertificateStatus": "off"}}).encode())
            if url == "https://cdn.aliyuncs.com/" and method == "POST":
                body_str = req.data.decode("utf-8")
                captured_posts.append((dict(req.header_items()), rec.urllib.parse.parse_qs(body_str)))
                return _FakeResponse(body=b'{"RequestId":"req-ssl"}')
            if "BatchSetCdnDomainConfig" in url:
                batch_urls.append(rec.urllib.parse.unquote(url))
                return _FakeResponse(body=b"{}")
            if "/dns_records" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "rec-1", "content": "cn-dl.svc.plus.queniuaa.com", "proxied": False}]}).encode())
            if "GetUser" in url or "GetPolicy" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if any(act in url for act in ("CreateUser", "CreatePolicy", "AttachPolicyToUser")):
                return _FakeResponse(body=b"{}")
            if "ListPoliciesForUser" in url:
                return _FakeResponse(body=json.dumps({"Policies": {"Policy": []}}).encode())
            if "CreateAccessKey" in url:
                return _FakeResponse(body=json.dumps({"AccessKey": {"AccessKeyId": "ram-ak-1", "AccessKeySecret": "ram-sk-1"}}).encode())
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")

        res = rec.reconcile(
            {
                "manifest_path": str(tls_manifest),
                "mode": "apply",
                "apply": True,
                "verify": False,
                "vault_addr": "https://vault.example",
                "vault_token": "mock-vault-token",
            },
            opener=tls_opener,
            wait_dns_seconds=0,
        )
        self.assertTrue(res["changed"])
        self.assertIn("cdn_tls:configured", res["aliyun_oss_cdn"]["actions"])
        self.assertEqual(len(captured_posts), 1)
        _, form = captured_posts[0]
        self.assertEqual(form["Action"], ["SetCdnDomainSSLCertificate"])
        self.assertEqual(form["CertType"], ["upload"])
        self.assertEqual(form["SSLProtocol"], ["on"])
        self.assertEqual(form["SSLPub"], [fake_pub])
        self.assertEqual(form["SSLPri"], [fake_pri])
        self.assertEqual(len(batch_urls), 1)
        self.assertIn("https_force", batch_urls[0])
        self.assertNotIn("MOCK-PRIVATE-KEY-SECRET", json.dumps(res))

    def test_custom_oss_policy_prefixes_probe_path_web_cdn_and_cname_polling(self):
        repos_manifest_text = """---
apiVersion: gitops.svc.plus/v1alpha1
kind: ClientMirrorStack
metadata:
  name: svc-plus-client-repos
  project: svc.plus
  environment: prod
spec:
  global:
    zone_name: svc.plus
    probe_path: " _health/probe.json "
    managed_by: playbooks-roles-saas-client_mirrors
  products:
    - slug: apt
      prefixes: [apt/]
  admin_vault_sources:
    cloudflare: kv/prod/serverless/cloudflare
    alicloud: kv/prod/platform/alicloud
  cloudflare_r2:
    bucket_name: svc-plus-repos-global
    custom_domain: repo.svc.plus
    location: apac
    min_tls: "1.2"
    cors_allowed_origins: [https://console.svc.plus]
    cors_allowed_methods: [GET, HEAD]
    cors_max_age_seconds: 3600
    token_name: svc-plus-client-repos-r2-rw
    vault_kv_path: prod/platform/storage/cloudflare-r2-repos
  aliyun_oss_cdn:
    bucket_name: svc-plus-repos-cn-hk
    oss_region: cn-hongkong
    oss_acl: public-read
    cdn_domain: cn-repo.svc.plus
    cdn_type: web
    cdn_scope: overseas
    cdn_rate_limit: 10m
    cdn_rate_limit_after: 1m
    cdn_cas_cert_id: ""
    cors_allowed_origins: [https://console.svc.plus]
    cors_allowed_methods: [GET, HEAD]
    cors_max_age_seconds: 3600
    ram_user_name: ci-client-repos-oss-rw
    ram_policy_name: ci-client-repos-oss-rw-policy
    oss_policy_prefixes:
      - apt/*
      - install.*
      - index.html
    vault_kv_path: prod/platform/storage/aliyun-oss-repos
"""
        repos_manifest = self.tmp / "repos.yaml"
        repos_manifest.write_text(repos_manifest_text, encoding="utf-8")

        parsed = rec.load_and_validate_manifest(repos_manifest)
        self.assertEqual(parsed["probe_path"], "_health/probe.json")
        self.assertEqual(parsed["ali"]["cdn_type"], "web")
        self.assertEqual(parsed["ali"]["oss_policy_prefixes"], ["apt/*", "install.*", "index.html"])

        for bad_prefixes in ("[]", '[""]', '["/apt/*"]', '["*"]', '["**"]', '["../secret"]'):
            bad_manifest = self.tmp / "bad-prefixes.yaml"
            bad_manifest.write_text(
                repos_manifest_text.replace(
                    "    oss_policy_prefixes:\n      - apt/*\n      - install.*\n      - index.html",
                    f"    oss_policy_prefixes: {bad_prefixes}",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError, msg=f"Expected rejection for {bad_prefixes}"):
                rec.load_and_validate_manifest(bad_manifest)

        calls = []
        detail_calls = 0
        dns_creates = []

        def repos_opener(req, timeout=25):
            nonlocal detail_calls
            method = req.get_method()
            url = req.full_url
            calls.append((method, url))
            if "/v1/kv/data/prod/serverless/cloudflare" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"CLOUDFLARE_API_TOKEN": "cf-adm", "CLOUDFLARE_ACCOUNT_ID": "acct-1"}}}).encode())
            if "/v1/kv/data/prod/platform/alicloud" in url:
                return _FakeResponse(body=json.dumps({"data": {"data": {"access_key_id": "ali-adm-ak", "access_key_secret": "ali-adm-sk"}}}).encode())
            if "/v1/kv/data/prod/platform/storage/" in url:
                if method == "POST":
                    return _FakeResponse(body=b"{}")
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "/zones?name=svc.plus" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "zone-1"}]}).encode())
            if url.endswith("/r2/buckets/svc-plus-repos-global") and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"name": "svc-plus-repos-global"}}).encode())
            if "/domains/custom" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": {"domains": [{"domain": "repo.svc.plus", "enabled": True, "minTLS": "1.2"}]}}).encode())
            if "/cors" in url:
                return _FakeResponse(
                    body=json.dumps(
                        {
                            "success": True,
                            "result": {
                                "rules": [
                                    {
                                        "id": "client-mirrors-cors",
                                        "allowed": {
                                            "methods": ["GET", "HEAD"],
                                            "origins": ["https://console.svc.plus"],
                                            "headers": ["*"],
                                        },
                                        "exposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                                        "maxAgeSeconds": 3600,
                                    }
                                ]
                            },
                        }
                    ).encode()
                )
            if "/tokens/permission_groups" in url:
                return _FakeResponse(body=json.dumps({"success": True, "result": [{"id": "pg-r2-w", "name": "Workers R2 Storage Bucket Item Write"}]}).encode())
            if url.endswith("/tokens") and method == "POST":
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "r2-tok-id", "value": "r2-raw-tok"}}).encode())
            if "oss-cn-hongkong" in url and method == "HEAD":
                return _FakeResponse(status=200, body=b"")
            if "oss-cn-hongkong" in url and method in ("DELETE", "PUT"):
                return _FakeResponse(status=200, body=b"")
            if "DescribeVerifyContent" in url:
                return _FakeResponse(body=json.dumps({"Content": ""}).encode())
            if "DescribeCdnDomainDetail" in url:
                detail_calls += 1
                if detail_calls == 1:
                    raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                if detail_calls == 2:
                    return _FakeResponse(body=json.dumps({"GetDomainDetailModel": {"Cname": ""}}).encode())
                return _FakeResponse(body=json.dumps({"GetDomainDetailModel": {"Cname": "cn-repo.svc.plus.w.alikunlun.com"}}).encode())
            if "AddCdnDomain" in url:
                return _FakeResponse(body=json.dumps({"RequestId": "req-add"}).encode())
            if "BatchSetCdnDomainConfig" in url:
                return _FakeResponse(body=b"{}")
            if "/dns_records" in url and method == "GET":
                return _FakeResponse(body=json.dumps({"success": True, "result": []}).encode())
            if "/dns_records" in url and method == "POST":
                dns_creates.append(json.loads(req.data.decode("utf-8")))
                return _FakeResponse(body=json.dumps({"success": True, "result": {"id": "rec-1"}}).encode())
            if "GetUser" in url or "GetPolicy" in url:
                raise rec.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if any(act in url for act in ("CreateUser", "CreatePolicy", "AttachPolicyToUser")):
                return _FakeResponse(body=b"{}")
            if "ListPoliciesForUser" in url:
                return _FakeResponse(body=json.dumps({"Policies": {"Policy": []}}).encode())
            if "CreateAccessKey" in url:
                return _FakeResponse(body=json.dumps({"AccessKey": {"AccessKeyId": "ram-ak-1", "AccessKeySecret": "ram-sk-1"}}).encode())
            if "r2.cloudflarestorage.com" in url and method == "PUT":
                return _FakeResponse(status=200, body=b"")
            if url in ("https://repo.svc.plus/_health/probe.json", "https://cn-repo.svc.plus/_health/probe.json"):
                return _FakeResponse(status=200, body=b'{"status":"ok"}', headers={"Access-Control-Allow-Origin": "https://console.svc.plus"})
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")

        res = rec.reconcile(
            {
                "manifest_path": str(repos_manifest),
                "mode": "apply",
                "apply": True,
                "verify": True,
                "vault_addr": "https://vault.example",
                "vault_token": "mock-vault-token",
            },
            opener=repos_opener,
            wait_dns_seconds=0,
        )
        self.assertEqual(detail_calls, 3)
        self.assertIn("cdn_domain:created", res["aliyun_oss_cdn"]["actions"])
        self.assertIn("cdn_cname:created", res["aliyun_oss_cdn"]["actions"])
        self.assertEqual(res["aliyun_oss_cdn"]["cdn_cname"], "cn-repo.svc.plus.w.alikunlun.com")
        self.assertEqual(len(dns_creates), 1)
        self.assertEqual(dns_creates[0]["content"], "cn-repo.svc.plus.w.alikunlun.com")
        add_cdn_urls = [url for _, url in calls if "Action=AddCdnDomain&" in url]
        self.assertEqual(len(add_cdn_urls), 1)
        self.assertIn("CdnType=web", rec.urllib.parse.unquote(add_cdn_urls[0]))

        create_policy_urls = [url for _, url in calls if "Action=CreatePolicy&" in url]
        self.assertEqual(len(create_policy_urls), 1)
        policy_qs = rec.urllib.parse.parse_qs(rec.urllib.parse.urlparse(create_policy_urls[0]).query)
        policy_doc = json.loads(policy_qs["PolicyDocument"][0])
        self.assertEqual(
            policy_doc["Statement"][0]["Resource"],
            [
                "acs:oss:*:*:svc-plus-repos-cn-hk/apt/*",
                "acs:oss:*:*:svc-plus-repos-cn-hk/install.*",
                "acs:oss:*:*:svc-plus-repos-cn-hk/index.html",
            ],
        )
        self.assertEqual(
            policy_doc["Statement"][1]["Condition"],
            {"StringLike": {"oss:Prefix": ["apt/*", "install.*", "index.html"]}},
        )

        self.assertEqual(
            [p["url"] for p in res["verification"]["https_probes"]],
            ["https://repo.svc.plus/_health/probe.json", "https://cn-repo.svc.plus/_health/probe.json"],
        )
        self.assertTrue(all(p["ok"] for p in res["verification"]["https_probes"]))

    def test_probe_mirrors_retries_transient_errors_when_wait_dns_positive(self):
        attempts = {"repo.svc.plus": 0, "cn-repo.svc.plus": 0}

        def flaky_opener(req, timeout=25):
            method = req.get_method()
            url = req.full_url
            if method == "PUT":
                return _FakeResponse(status=200, body=b"")
            if "https://repo.svc.plus/" in url:
                attempts["repo.svc.plus"] += 1
                if attempts["repo.svc.plus"] == 1:
                    raise ssl.SSLError("handshake failure")
                if attempts["repo.svc.plus"] == 2:
                    raise rec.urllib.error.URLError("dns not ready")
                return _FakeResponse(status=200, body=b"{}", headers={"Access-Control-Allow-Origin": "https://console.svc.plus"})
            if "https://cn-repo.svc.plus/" in url:
                attempts["cn-repo.svc.plus"] += 1
                if attempts["cn-repo.svc.plus"] == 1:
                    raise rec.urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
                return _FakeResponse(status=200, body=b"{}", headers={"Access-Control-Allow-Origin": "https://console.svc.plus"})
            raise AssertionError(f"Unexpected call: {method} {url}")

        with mock.patch.object(rec.time, "sleep") as sleep_mock:
            probe_res = rec._probe_mirrors(
                r2_creds={
                    "endpoint": "https://acct-1.r2.cloudflarestorage.com",
                    "bucket": "svc-plus-repos-global",
                    "custom_domain": "repo.svc.plus",
                    "access_key_id": "ak",
                    "secret_access_key": "sk",
                },
                ali_creds={
                    "bucket": "svc-plus-repos-cn-hk",
                    "oss_region": "cn-hongkong",
                    "cdn_domain": "cn-repo.svc.plus",
                    "access_key_id": "ali-ak",
                    "access_key_secret": "ali-sk",
                },
                probe_path="_health/probe.json",
                probe_origin="https://console.svc.plus",
                opener=flaky_opener,
                wait_dns_seconds=15,
            )
        self.assertEqual(attempts, {"repo.svc.plus": 3, "cn-repo.svc.plus": 2})
        self.assertEqual(sleep_mock.call_count, 3)
        sleep_mock.assert_called_with(10)
        self.assertTrue(all(p["ok"] for p in probe_res["https_probes"]))


if __name__ == "__main__":
    unittest.main()
