"""Offline Playbooks checks for the trusted SIT feature lane domain contract."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def domains_for(feature_id):
    if not re.fullmatch(r"feature-[a-z][a-z0-9-]{1,48}", feature_id) or feature_id.endswith("-"):
        raise ValueError("invalid Feature ID")
    slug = feature_id.removeprefix("feature-")
    return {
        "console": f"{slug}-sit.onwalk.net",
        "accounts": f"accounts-{slug}-sit.onwalk.net",
        "billing": f"billing-{slug}-sit.onwalk.net",
    }


class FeatureDomainContract(unittest.TestCase):
    def test_current_feature_uses_existing_referral_hosts(self):
        self.assertEqual(domains_for("feature-xconnect-referral"), {
            "console": "xconnect-referral-sit.onwalk.net",
            "accounts": "accounts-xconnect-referral-sit.onwalk.net",
            "billing": "billing-xconnect-referral-sit.onwalk.net",
        })

    def test_other_identifiers_only_produce_dns_names(self):
        for feature_id in ("feature-search", "feature-checkout-v2"):
            with self.subTest(feature_id=feature_id):
                domains = domains_for(feature_id)
                self.assertEqual(set(domains), {"console", "accounts", "billing"})
                self.assertTrue(all(host.endswith(".onwalk.net") for host in domains.values()))

    def test_unsafe_identifiers_fail_closed(self):
        for feature_id in ("../prod", "Feature-A", "feature-", "feature-a_"):
            with self.subTest(feature_id=feature_id), self.assertRaises(ValueError):
                domains_for(feature_id)

    def test_live_owner_remains_fixed_to_declared_referral_lane(self):
        scope = (ROOT / "roles/platform/dev_feature_flux/files/reconcile.py").read_text()
        candidate = (ROOT / "scripts/dev_feature_candidate.py").read_text()
        self.assertIn("feature-xconnect-referral-sit", scope)
        self.assertIn("public hostname not in trusted onwalk.net lane allowlist", candidate)
        # Domain syntax alone is not permission to provision arbitrary namespaces.
        self.assertIn("fixed Dev lane scope required", scope)


if __name__ == "__main__":
    unittest.main()
