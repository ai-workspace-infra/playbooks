import json
import os
from pathlib import Path
import subprocess
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'scripts/docker-credential-feature-baseline'

class CredentialHelperTest(unittest.TestCase):
    def test_only_get_for_ghcr_returns_in_memory_credentials(self):
        env = dict(os.environ, FEATURE_GHCR_USERNAME='fixture-user', FEATURE_GHCR_TOKEN='fixture-token')
        good = subprocess.run([str(HELPER), 'get'], input='ghcr.io\n', text=True, capture_output=True, env=env)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(json.loads(good.stdout), {'Username':'fixture-user', 'Secret':'fixture-token'})
        for operation, host in [('get','foreign.example'), ('store','ghcr.io'), ('erase','ghcr.io')]:
            result = subprocess.run([str(HELPER), operation], input=host+'\n', text=True, capture_output=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

if __name__ == '__main__': unittest.main()
