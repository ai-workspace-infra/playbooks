#!/usr/bin/env python3
"""Keep every V4 incident linked to a concrete repeatable regression gate."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
CASE_DOC = ROOT / "docs/cases/runbooks/2026-10-21-v4-p0-regression-cases.md"


class V4RegressionCaseCatalogTest(unittest.TestCase):
    def test_every_incident_has_a_case_and_test_reference(self):
        text = CASE_DOC.read_text()
        cases = {
            "V4-P0-DB-ROLE": "v4_business_role_acceptance.sh",
            "V4-P0-STRIPE-EGRESS": "v4_stripe_sandbox_egress.sh",
            "V4-P0-WEBHOOK-SECRET": "api/billing_p1_test.go",
            "V4-P0-AGENT-INGRESS": "authenticated agent status POST",
            "V4-P0-EXPORTER-TOKEN": "tests/test_snapshot_credential_repair.py",
            "V4-P0-TRAFFIC-CHAIN": "tests/data_operations/selfhost/v4_traffic_chain_acceptance.sh",
            "V4-P0-DUPLICATE-NODE": "tests/data_operations/selfhost/v4_duplicate_producer_identity_test.py",
            "V4-P0-UI-FRESHNESS": "Authenticated browser acceptance",
            "V4-P0-LOGIN-SESSION": "Candidate login integration",
            "V4-P0-ACCOUNT-BOOTSTRAP": "Authenticated onboarding browser/API test",
        }
        for case_id, test_reference in cases.items():
            with self.subTest(case_id=case_id):
                self.assertIn(case_id, text)
                self.assertIn(test_reference, text)

    def test_release_gate_rejects_nonpassing_cases(self):
        text = CASE_DOC.read_text()
        self.assertIn("BLOCKED | NOT_RUN", text)
        self.assertIn("Neither passes the release gate", text)

    def test_history_link_points_to_the_v2_v3_retrospective(self):
        self.assertIn("15-prod-v2-v3-snapshots-and-upgrade-retrospective.zh.md", CASE_DOC.read_text())


if __name__ == "__main__":
    unittest.main()
