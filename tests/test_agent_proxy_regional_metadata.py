"""Render the deployed agent template through Ansible, without running a role."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class AgentRegionalMetadataTest(unittest.TestCase):
    def test_reported_entry_can_differ_from_node_identity_and_stay_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'account-agent.yaml'
            playbook = Path(directory) / 'render.yaml'
            overrides = {'agent_id': 'physical-node', 'agent_controller_url': 'https://uat-accounts-example.run.app', 'agent_api_token': 'test-placeholder'}
            playbook.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'gather_facts': False,
                'vars': {'agent_id': 'physical-node', 'xconnect_region': 'sg',
                         'xconnect_pool': 'Singapore pool',
                         'xconnect_fqdn': 'sg-xconnect.onwalk.net',
                         'xconnect_open_to_users': False,
                         'agent_controller_url': 'https://uat-accounts-example.run.app',
                         'agent_api_token': 'test-placeholder'},
                'tasks': [
                    {'ansible.builtin.include_vars': str(ROOT / 'roles/vhosts/agent-proxy/defaults/main.yml')},
                    {'ansible.builtin.template': {'src': str(ROOT / 'roles/vhosts/agent-proxy/templates/agent-config.yaml.j2'), 'dest': str(output)}},
                ],
            }]))
            subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook), '-e', json.dumps(overrides)], check=True, capture_output=True, text=True)
            agent = yaml.safe_load(output.read_text())['agent']
            self.assertEqual(agent['id'], 'physical-node')
            self.assertEqual(agent['region'], 'sg')
            self.assertEqual(agent['pool'], 'Singapore pool')
            self.assertEqual(agent['entryPoint'], 'sg-xconnect.onwalk.net')
            self.assertFalse(agent['openToUsers'])


if __name__ == '__main__':
    unittest.main()
