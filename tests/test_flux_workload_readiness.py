"""Exercise readiness against a fake Kubernetes API via kubectl, without a cluster."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FluxReadiness(unittest.TestCase):
    def run_observation(self, stale=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tool = path / 'kubectl'
            tool.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv
if 'wait' in args:
    sys.exit(0)
kind, name = args[args.index('get')+1:args.index('get')+3]
revision = 'main@sha1:' + ('b' if os.environ.get('FAKE_FLUX_STALE') and kind == 'kustomizations' else 'a') * 40
status = {'observedGeneration': 1, 'conditions': [{'type': 'Ready', 'status': 'True'}]}
if kind == 'gitrepositories': status['artifact'] = {'revision': revision}
if kind == 'kustomizations': status['lastAppliedRevision'] = revision
print(json.dumps({'metadata': {'generation': 1}, 'spec': {'sourceRef': {'name': 'source'}}, 'status': status}))
''')
            tool.chmod(0o755)
            receipt = path / 'receipt.json'
            play = path / 'test.yml'
            play.write_text('''- hosts: localhost
  gather_facts: false
  vars:
    k3s_platform_kubeconfig_path: /unused
    k3s_platform_git_commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    k3s_platform_receipt_path: "%s"
    k3s_platform_acceptance_objects:
      - {kind: gitrepositories, namespace: flux-system, name: source}
      - {kind: kustomizations, namespace: flux-system, name: unit}
      - {kind: helmreleases, namespace: apps, name: application}
  roles:
    - vhosts/flux_workload_readiness
''' % receipt)
            env = dict(os.environ, ANSIBLE_ROLES_PATH=str(ROOT / 'roles'), PATH=str(path) + os.pathsep + os.environ['PATH'])
            if stale:
                env['FAKE_FLUX_STALE'] = '1'
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)], env=env, capture_output=True, text=True, timeout=40)
            evidence = json.loads(Path(str(receipt) + '.flux-readiness.json').read_text()) if result.returncode == 0 else None
            return result, evidence

    def test_ready_objects_emit_separate_non_runtime_receipt(self):
        result, evidence = self.run_observation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(evidence['result'], 'FLUX_OBJECTS_READY')
        self.assertEqual(evidence['runtime_acceptance'], 'NOT_RUN')
        self.assertEqual(len(evidence['objects']), 3)

    def test_stale_unit_does_not_emit_success_receipt(self):
        result, evidence = self.run_observation(stale=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(evidence)


if __name__ == '__main__':
    unittest.main()
