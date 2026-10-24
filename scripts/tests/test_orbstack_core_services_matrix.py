"""Contract tests for 11 core platform services OrbStack deployment and compose configurations."""
import os
from pathlib import Path
import re
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
VERIFY_DIR = SCRIPTS_DIR / "orbstack-verification"
VERIFY_SCRIPT = VERIFY_DIR / "verify-orbstack-all-services.sh"


class TestOrbStack11CoreServicesMatrix(unittest.TestCase):
    def test_verification_script_and_compose_directories(self):
        self.assertTrue(VERIFY_SCRIPT.exists(), f"Verification script missing: {VERIFY_SCRIPT}")
        self.assertTrue(os.access(VERIFY_SCRIPT, os.X_OK), "Verification script must be executable")

        expected_dirs = [
            "01-git-ci-gitea",
            "02-artifact-harbor",
            "03-s3-storage-silo",
            "04-cmdb-inventory",
            "05-observability",
            "06-identity-vault-zitadel",
            "07-gitops-doco-cd",
            "08-container-vector-node",
            "09-pg-backup-drill",
            "10-ai-gateway",
            "11-mcp-server",
        ]
        assigned_ports = set()
        for d in expected_dirs:
            compose_file = VERIFY_DIR / d / "docker-compose.yml"
            self.assertTrue(compose_file.exists(), f"Missing docker-compose.yml in {d}")
            compose_text = compose_file.read_text(encoding="utf-8")
            # Extract published host ports (e.g. "127.0.0.1:13000:3000")
            ports = re.findall(r'127\.0\.0\.1:(\d+):', compose_text)
            self.assertTrue(len(ports) > 0, f"No host loopback ports found in {compose_file}")
            for p in ports:
                self.assertNotIn(p, assigned_ports, f"Duplicate port collision detected: {p} in {d}")
                assigned_ports.add(p)

        # Ensure all key platform ports are assigned
        critical_ports = {
            "13000", "13001", "13030", "14000",
            "15434", "15435", "15436",
            "18000", "18080", "18200", "18686", "18888",
            "19000", "19001", "19088", "19090", "19100", "19128", "19428",
            "10428"
        }
        self.assertTrue(critical_ports.issubset(assigned_ports), f"Missing critical ports: {critical_ports - assigned_ports}")

    def test_all_11_service_images_pinned(self):
        expected_pinned_images = {
            "01-git-ci-gitea": ["gitea/gitea:1.22.6"],
            "02-artifact-harbor": ["goharbor/harbor-portal:v2.15.4"],
            "03-s3-storage-silo": ["pgsty/silo:RELEASE.2026-10-18T00-00-00Z"],
            "04-cmdb-inventory": ["python:3.11-alpine"],
            "05-observability": [
                "victoriametrics/victoria-metrics:v1.101.0",
                "victoriametrics/victoria-logs:v1.52.0",
                "victoriametrics/victoria-traces:v0.1.0",
                "grafana/grafana:11.3.0",
                "prom/node-exporter:v1.8.2",
                "timberio/vector:0.41.1-alpine",
            ],
            "06-identity-vault-zitadel": ["hashicorp/vault:1.17.6", "postgres:16-alpine"],
            "07-gitops-doco-cd": ["ghcr.io/kimdre/doco-cd:0.1.28"],
            "08-container-vector-node": ["pgvector/pgvector:pg16"],
            "09-pg-backup-drill": ["postgres:16-alpine"],
            "10-ai-gateway": ["calciumion/new-api:v0.4.10", "ghcr.io/berriai/litellm:v1.51.0"],
            "11-mcp-server": ["alpine:3.20"],
        }
        for d, images in expected_pinned_images.items():
            compose_file = VERIFY_DIR / d / "docker-compose.yml"
            content = compose_file.read_text(encoding="utf-8")
            for img in images:
                self.assertIn(img, content, f"Image {img} not found in {compose_file}")


if __name__ == "__main__":
    unittest.main()
