import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/probe_feature_seed_vault.py'
spec = importlib.util.spec_from_file_location('seed_probe', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SeedProbeTest(unittest.TestCase):
    def test_allowed_target_and_denied_adjacent_path(self):
        oidc = (200, json.dumps({'value':'test-jwt'}).encode())
        login = (200, json.dumps({'auth':{'client_token':'test-token'}}).encode())
        env = {'ACTIONS_ID_TOKEN_REQUEST_URL':'https://example.invalid/oidc', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN':'test'}
        with patch.dict('os.environ', env), patch.object(probe, 'request', side_effect=[oidc, login, (200,b''), (403,b'')]) as request:
            probe.main()
        self.assertEqual(json.loads(request.call_args_list[1].args[2])['role'], 'github-actions-playbooks-feature-seed-sit')
        with patch.dict('os.environ', env), patch.object(probe, 'request', side_effect=[oidc, login, (200,b''), (200,b'')]):
            with self.assertRaises(ValueError): probe.main()


if __name__ == '__main__':
    unittest.main()
