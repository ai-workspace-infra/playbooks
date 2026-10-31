"""Exercise the read-only reset classification without running cleanup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ResetDecision(unittest.TestCase):
    def run_case(self, *, path=False, binary_rc=1, services=('not-found', 'not-found'), service_rc=0, expected=False):
        with tempfile.TemporaryDirectory() as name:
            temp = Path(name)
            config = temp / 'ansible.cfg'
            config.write_text('[defaults]\nstdout_callback=default\n')
            play = temp / 'check.yml'
            variables = {'k3s_reset_binary': {'rc': binary_rc},
                'k3s_reset_paths': {'results': [{'stat': {'exists': path}}]},
                'k3s_reset_services': {'results': [{'rc': service_rc, 'stdout': value} for value in services]}}
            tasks = [{'ansible.builtin.import_role': {'name': 'vhosts/k3s-reset', 'tasks_from': 'decide.yml'}},
                     {'ansible.builtin.assert': {'that': [f'k3s_reset_present | bool == {str(expected).lower()}']}}]
            play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                                             'vars': variables, 'tasks': tasks}]))
            return subprocess.run([sys.executable, '-m', 'ansible.cli.playbook', '-i', 'localhost,',
                                   '-c', 'local', str(play)], cwd=ROOT, capture_output=True, text=True,
                                  env={**os.environ, 'ANSIBLE_CONFIG': str(config),
                                       'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')}, timeout=30)

    def test_absent_and_installed_states(self):
        cases = [{}, {'binary_rc': 127}, {'path': True, 'expected': True}, {'binary_rc': 0, 'expected': True},
                 {'services': ('loaded', 'not-found'), 'expected': True},
                 {'services': ('masked', 'not-found'), 'expected': True}]
        for case in cases:
            with self.subTest(case=case):
                result = self.run_case(**case)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_inconclusive_inspection_fails_closed(self):
        for case in ({'service_rc': 1}, {'binary_rc': 2}, {'services': ('unknown', 'not-found')}):
            with self.subTest(case=case):
                result = self.run_case(**case)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
