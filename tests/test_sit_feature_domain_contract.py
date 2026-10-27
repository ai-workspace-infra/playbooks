"""Offline checks for the shared SIT feature domain derivation; no DNS or cluster access."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("feature_dns", ROOT.parent / "iac-sit-feature-dns/scripts/pipeline/cloudflare-sit-feature-dns.py")
feature_dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feature_dns)


class FeatureDomainContract(unittest.TestCase):
    def test_current_feature_uses_existing_referral_hosts(self):
        self.assertEqual(feature_dns.lane_domain("feature-xconnect-referral", "console", "onwalk.net"), "xconnect-referral-sit.onwalk.net")
        self.assertEqual(feature_dns.lane_domain("feature-xconnect-referral", "accounts", "onwalk.net"), "accounts-xconnect-referral-sit.onwalk.net")
        self.assertEqual(feature_dns.lane_domain("feature-xconnect-referral", "billing", "onwalk.net"), "billing-xconnect-referral-sit.onwalk.net")

    def test_new_feature_namespaces_are_dns_safe_but_do_not_authorize_cluster_use(self):
        for feature_id in ("feature-search", "feature-checkout-v2"):
            slug = feature_id.removeprefix("feature-")
            for service in ("console", "accounts", "billing"):
                self.assertTrue(feature_dns.lane_domain(feature_id, service, "onwalk.net").endswith(".onwalk.net"))
            self.assertEqual(f"feature-{slug}-sit", f"feature-{slug}-sit")

    def test_unsafe_identifiers_fail_closed(self):
        for feature_id in ("../prod", "Feature-A", "feature-", "feature-a_"):
            with self.subTest(feature_id=feature_id), self.assertRaises(feature_dns.base.OperationError):
                feature_dns.lane_domain(feature_id, "console", "onwalk.net")


if __name__ == "__main__":
    unittest.main()
