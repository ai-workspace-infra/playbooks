import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]

class InitializationScopeTests(unittest.TestCase):
    def validate(self, **changes):
        env = dict(os.environ, REQUESTED_ENVIRONMENT='uat', REQUESTED_OPERATION='selfhost_init',
                   CONFIG_JSON='{"target_host":"web-saas-uat","caller_run_id":"123"}',
                   CORRELATION_ID='test-init', RELEASE_TAG='daily-build-2026.10.06-r2',
                   ACCOUNTS_REF='daily-build-2026.10.06-r2')
        env.update(changes)
        return subprocess.run(['python3', str(ROOT / 'scripts/data_operations/selfhost/validate_operation.py')],
                              env=env, text=True, capture_output=True)

    def test_init_is_uat_only(self):
        result = self.validate(REQUESTED_ENVIRONMENT='prod', CONFIG_JSON='{"target_host":"web-saas-prod","caller_run_id":"123"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('UAT-only', result.stderr)

    def test_schema_must_match_deployed_release(self):
        result = self.validate(RELEASE_TAG='daily-build-2026.10.05-r1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('equal release_tag', result.stderr)

    def test_exact_release_is_accepted(self):
        self.assertEqual(self.validate().returncode, 0)
