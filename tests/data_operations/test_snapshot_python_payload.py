"""Compile the actual Ansible-rendered capture payload without executing it."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class SnapshotPayloadTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ansible-playbook'), 'requires the Ansible rendering runtime')
    def test_actual_template_renders_compilable_python(self):
        role = ROOT / 'roles/web_saas_full_snapshot'
        tasks = yaml.safe_load((role / 'tasks/main.yml').read_text())
        capture = next(t for t in tasks if t['name'].startswith('Capture encrypted'))
        payload = capture['ansible.builtin.command']['argv'][2]
        play = [{'hosts': 'localhost', 'gather_facts': False,
                 'vars': {'role_path': str(role)},
                 'tasks': [{'ansible.builtin.command': {
                     'argv': ['python3', '-c', 'import ast,sys; ast.parse(sys.stdin.read())'],
                     'stdin': payload}, 'changed_when': False}]}]
        with tempfile.TemporaryDirectory(prefix='snapshot-payload-') as directory:
            path = Path(directory) / 'compile.yml'
            path.write_text(yaml.safe_dump(play))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
