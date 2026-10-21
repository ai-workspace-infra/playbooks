import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/web_saas_content_runtime/files/verify_runtime.py"
IMAGE = "ghcr.io/ai-workspace-services/docs:content-test@sha256:" + "a" * 64
TOKEN = "sample-secret-that-must-not-be-printed"


def inspect_payload():
    rows = []
    for name in ("accounts", "billing", "console", "content"):
        networks = {"web_saas_recovery": {}}
        if name == "content":
            networks["web_saas_content_egress"] = {}
        env = ["INTERNAL_SERVICE_TOKEN=" + TOKEN]
        image = IMAGE if name == "content" else "example.invalid/" + name
        if name == "console":
            env.extend(
                [
                    "DOCS_SERVICE_URL=http://content:8084",
                    "RUNTIME_DOCS_SERVICE_URL=http://content:8084",
                ]
            )
        rows.append(
            {
                "Name": "/web-saas-" + name,
                "Config": {"Image": image, "Env": env},
                "State": {
                    "Running": True,
                    "Health": {"Status": "healthy"} if name == "content" else {},
                },
                "NetworkSettings": {"Networks": networks},
            }
        )
    return rows


class WebSaaSContentRuntimeTests(unittest.TestCase):
    def run_verifier(self, payload):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--expected-image", IMAGE],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_selfhost_content_runtime_and_emits_sanitized_evidence(self):
        result = self.run_verifier(inspect_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "passed"', result.stdout)
        self.assertIn('"shared_token_consistent": true', result.stdout)
        self.assertIn(IMAGE.rsplit("@", 1)[1], result.stdout)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_rejects_cloud_run_console_endpoint(self):
        payload = inspect_payload()
        console = next(row for row in payload if row["Name"] == "/web-saas-console")
        console["Config"]["Env"] = [
            item.replace("http://content:8084", "https://legacy.run.app")
            for item in console["Config"]["Env"]
        ]
        result = self.run_verifier(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_rejects_missing_health_and_egress_leakage(self):
        payload = inspect_payload()
        content = next(row for row in payload if row["Name"] == "/web-saas-content")
        content["State"]["Health"]["Status"] = "starting"
        accounts = next(row for row in payload if row["Name"] == "/web-saas-accounts")
        accounts["NetworkSettings"]["Networks"]["web_saas_content_egress"] = {}
        result = self.run_verifier(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_rejects_a_mismatched_shared_service_token(self):
        payload = inspect_payload()
        billing = next(row for row in payload if row["Name"] == "/web-saas-billing")
        billing["Config"]["Env"][0] = "INTERNAL_SERVICE_TOKEN=another-secret"
        result = self.run_verifier(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
