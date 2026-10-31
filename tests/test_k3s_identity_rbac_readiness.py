"""Exercise the Flux readiness gate without contacting a cluster."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class Readiness(unittest.TestCase):
    def run_gate(self, obj):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'kubectl'
            binary.write_text('#!/bin/sh\nprintf %s ' + "'" + json.dumps(obj) + "'\n")
            binary.chmod(0o755)
            play = root / 'test.yml'
            play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False,
                'vars': {'k3s_identity_rbac_kustomizations': [{'name': 'identities', 'namespace': 'flux-system'}],
                         'k3s_identity_rbac_revision': SHA, 'k3s_identity_rbac_wait_retries': 1,
                         'k3s_identity_rbac_wait_delay': 1},
                'tasks': [{'ansible.builtin.import_role': {'name': 'vhosts/k3s_identity', 'tasks_from': 'rbac_wait.yml'}}]}]))
            config = root / 'ansible.cfg'
            config.write_text('[defaults]\nstdout_callback=default\n')
            return subprocess.run([sys.executable, '-m', 'ansible.cli.playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                cwd=ROOT, env={**os.environ, 'ANSIBLE_CONFIG': str(config), 'ANSIBLE_ROLES_PATH': str(ROOT/'roles'),
                               'PATH': str(root) + os.pathsep + os.environ['PATH']}, capture_output=True, text=True, timeout=30)

    def test_ready_requires_current_generation_and_exact_commit(self):
        good = {'metadata': {'generation': 2}, 'status': {'observedGeneration': 2,
                'lastAppliedRevision': 'main@sha1:' + SHA, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        result = self.run_gate(good)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for field, value in [('observedGeneration', 1), ('lastAppliedRevision', 'main@sha1:' + 'b' * 40),
                             ('conditions', [{'type': 'Ready', 'status': 'False'}])]:
            wrong = json.loads(json.dumps(good))
            wrong['status'][field] = value
            with self.subTest(field=field):
                result = self.run_gate(wrong)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
