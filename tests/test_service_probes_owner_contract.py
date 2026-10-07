import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ServiceProbeOwnerContractTests(unittest.TestCase):
    def test_action_owns_registered_probes_and_binds_receipt(self):
        action = yaml.safe_load((ROOT / '.github/actions/service-probes/action.yml').read_text())
        source = (ROOT / '.github/actions/service-probes/action.yml').read_text()
        description = action['inputs']['operation']['description']
        self.assertIn('frontend-assets', description)
        self.assertIn('xconnect-control-plane', description)
        self.assertIn('SERVICE_PROBE_OWNER_SHA', source)
        self.assertIn('GITHUB_RUN_ATTEMPT', source)
        self.assertIn('owner-receipt-service-', source)
        self.assertNotIn('accept-new', source)

    def test_control_plane_probe_is_anonymous_and_read_only(self):
        source = (ROOT / 'scripts/pipeline/probe-xconnect-control-plane.sh').read_text()
        self.assertIn('[[ "$status" == 401 ]]', source)
        for method in ('--request POST', '--request PUT', '--request PATCH', '--request DELETE'):
            self.assertNotIn(method, source)

    def test_frontend_probe_requires_both_routes_and_both_css_assets(self):
        source = (ROOT / 'scripts/pipeline/verify-frontend-boundary-assets.sh').read_text()
        for contract in ('ssr-public', 'ssr-console', 'public_css_status', 'console_css_status',
                         'Frontend asset acceptance is unverified'):
            self.assertIn(contract, source)


if __name__ == '__main__':
    unittest.main()
