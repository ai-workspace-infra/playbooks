import copy
import grp
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/xray-exporter'
TOKEN = 'offline-fixture-' + 'a' * 40


class SnapshotCredentialRepairTests(unittest.TestCase):
    def run_play(self, path, tasks, variables):
        play = path / 'play.yml'
        play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False, 'vars': variables, 'tasks': tasks}]))
        return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)], capture_output=True, text=True, timeout=30)

    def test_metrics_defaults_cannot_erase_an_existing_snapshot_contract(self):
        guard = yaml.safe_load((ROLE / 'tasks/protect-existing-snapshots.yml').read_text())
        for enabled, credential, handoff, success in [
            (True, '', '', False), (True, TOKEN, '', False),
            (True, TOKEN, 'http://127.0.0.1:8686', True), (False, '', '', True),
        ]:
            with self.subTest(enabled=enabled, credential=bool(credential), handoff=handoff), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                env = path / 'exporter.env'
                original = 'INTERNAL_SERVICE_TOKEN=' + TOKEN + '\nVECTOR_SNAPSHOT_URL=' + ('http://127.0.0.1:8686' if enabled else '') + '\n'
                env.write_text(original)
                result = self.run_play(path, guard, {'xray_exporter_env_file': str(env), 'xray_exporter_internal_service_token': credential, 'xray_exporter_vector_snapshot_url': handoff})
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                self.assertEqual(env.read_text(), original)
                self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_binding_preserves_other_keys_and_repeats_without_change(self):
        tasks = yaml.safe_load((ROLE / 'tasks/repair-snapshot-credentials.yml').read_text())
        binding = copy.deepcopy(next(task for task in tasks if 'ansible.builtin.lineinfile' in task))
        binding['ansible.builtin.lineinfile']['owner'] = pwd.getpwuid(os.getuid()).pw_name
        binding['ansible.builtin.lineinfile']['group'] = grp.getgrgid(os.getgid()).gr_name
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            env = path / 'exporter.env'
            env.write_text('EXPORTER_NODE_ID=fixture\nINTERNAL_SERVICE_TOKEN=\nVECTOR_SNAPSHOT_URL=http://127.0.0.1:8686\n')
            variables = {'xray_exporter_env_file': str(env), 'canonical_internal_service_token': TOKEN}
            first = self.run_play(path, [binding], variables)
            second = self.run_play(path, [binding], variables)
            for result in [first, second]:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn(TOKEN, result.stdout + result.stderr)
            self.assertIn('changed=1', first.stdout)
            self.assertIn('changed=0', second.stdout)
            self.assertEqual(env.read_text(), 'EXPORTER_NODE_ID=fixture\nINTERNAL_SERVICE_TOKEN='+TOKEN+'\nVECTOR_SNAPSHOT_URL=http://127.0.0.1:8686\n')
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_repair_does_not_restart_xray_agent_or_vector(self):
        tasks = yaml.safe_load((ROLE / 'tasks/repair-snapshot-credentials.yml').read_text())
        restarts = [task for task in tasks if 'ansible.builtin.systemd' in task]
        self.assertEqual(len(restarts), 1)
        self.assertEqual(restarts[0]['when'], 'snapshot_repair_bound.changed')
        self.assertEqual(restarts[0]['loop'], '{{ xray_exporter_instances }}')
        probe = next(task for task in tasks if 'ansible.builtin.uri' in task)
        self.assertTrue(probe['no_log'])
        self.assertTrue(any('120' in rule for rule in probe['until']))
        self.assertNotIn('body', probe['ansible.builtin.uri'])
        self.assertEqual(probe['ansible.builtin.uri'].get('method', 'GET'), 'GET')
        self.assertEqual(probe['when'], 'not ansible_check_mode')
        self.assertFalse(binding_diff := next(task for task in tasks if 'ansible.builtin.lineinfile' in task)['diff'])
        entry = yaml.safe_load((ROOT / 'repair_xray_snapshot_credentials.yml').read_text())[0]
        self.assertEqual(entry['serial'], 1)
        self.assertIn('tasks/resolve-canonical-service-token.yml', json.dumps(entry))

    def test_check_mode_reads_fixed_inputs_and_vault_without_mutation(self):
        resolver = yaml.safe_load((ROOT / 'tasks/resolve-canonical-service-token.yml').read_text())
        read = next(task for task in resolver if 'ansible.builtin.command' in task)
        self.assertFalse(read['check_mode'])
        self.assertFalse(read['changed_when'])
        self.assertTrue(read['no_log'])
        entry = yaml.safe_load((ROOT / 'repair_xray_snapshot_credentials.yml').read_text())[0]
        for task in entry['pre_tasks']:
            if 'ansible.builtin.command' in task:
                self.assertFalse(task['check_mode'])
                self.assertFalse(task['changed_when'])


if __name__ == '__main__':
    unittest.main()
