import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TeamIdentityPreflight(unittest.TestCase):
    def execute(self, fields, profile='team'):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / 'vault'
            vault.write_text('#!/usr/bin/env python3\nimport os\nprint(os.environ["TEST_VAULT_JSON"])\n')
            vault.chmod(0o700)
            values = {'node_identity_hosts': 'localhost', 'node_identity_profile': profile,
                      'node_identity_id': 'test-node', 'node_identity_team_id': 'test-team',
                      'node_identity_credential_ref': 'kv/sit/xconnect/teams/test-team/nodes/test-node'}
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                       TEST_VAULT_JSON=json.dumps({'data': {'data': fields}}), ANSIBLE_NOCOLOR='1')
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local',
                                     'validate_xconnect_node_identity.yml', '-e', json.dumps(values)],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            if fields.get('token'):
                self.assertNotIn(fields['token'], result.stdout)
            return result

    def test_valid_team_scope(self):
        result = self.execute({'node_id': 'test-node', 'team_id': 'test-team',
                               'resource_class': 'team', 'token': 'synthetic-' + 'a' * 32})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('changed=0', result.stdout)

    def test_wrong_team_rejected(self):
        result = self.execute({'node_id': 'test-node', 'team_id': 'other-team',
                               'resource_class': 'team', 'token': 'synthetic-' + 'a' * 32})
        self.assertNotEqual(result.returncode, 0)

    def test_empty_token_rejected(self):
        result = self.execute({'node_id': 'test-node', 'team_id': 'test-team',
                               'resource_class': 'team', 'token': ''})
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
