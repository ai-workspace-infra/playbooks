"""Exercise the real volume target assertion without accessing a block device."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class K3sTarget(unittest.TestCase):
    def run_assertion(self, env, volume_id=22, group=None, instance_id=11):
        task = yaml.safe_load((ROOT / 'roles/web_saas_data_volume/tasks/main.yml').read_text())[0]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inventory = tmp / 'inventory.yml'
            groups = {'k3s': {'hosts': {'node-a': {}}}, group or env: {'hosts': {'node-a': {}}}}
            inventory.write_text(yaml.safe_dump({'all': {'children': groups}}))
            play = tmp / 'play.yml'
            variables = {'web_saas_data_target_host': 'node-a', 'web_saas_data_environment': env,
                'web_saas_data_allow_recovery_host': False, 'web_saas_data_allow_generation_host': True,
                'cmdb_instance_id': instance_id, 'web_saas_data_expected_volume_id': 22,
                'web_saas_data_device_path': '/dev/disk/by-id/test-volume',
                'data_volume': {'linode_id': instance_id, 'volume_id': volume_id,
                                'filesystem_path': '/dev/disk/by-id/test-volume'},
                'web_saas_data_action': 'inspect', 'web_saas_data_mount_path': '/data',
                'web_saas_data_filesystem_label': ''}
            play.write_text(yaml.safe_dump([{'hosts': 'node-a', 'gather_facts': False,
                                             'vars': variables, 'tasks': [task]}]))
            config = tmp / 'ansible.cfg'
            config.write_text('[defaults]\nstdout_callback=default\n')
            return subprocess.run([sys.executable, '-m', 'ansible.cli.playbook', '-i', str(inventory), str(play),
                                   '-c', 'local'], cwd=ROOT, capture_output=True, text=True, timeout=30,
                                  env={**os.environ, 'ANSIBLE_CONFIG': str(config)})

    def test_environment_scoped_host_and_volume_binding(self):
        for env in ('uat', 'prod'):
            with self.subTest(environment=env):
                result = self.run_assertion(env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.run_assertion('prod', volume_id=23).returncode, 0)
        self.assertNotEqual(self.run_assertion('prod', group='uat').returncode, 0)
        for instance_id in (None, '', 0):
            with self.subTest(instance_id=instance_id):
                self.assertNotEqual(self.run_assertion('prod', instance_id=instance_id).returncode, 0)


if __name__ == '__main__':
    unittest.main()
