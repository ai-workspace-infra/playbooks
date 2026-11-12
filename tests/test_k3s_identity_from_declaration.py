"""k3s_identity_bootstrap.yml binds every identity input from the GitOps declaration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
DECLARATION = {
    'global': {'environment': 'sit', 'project': 'svc.plus'},
    'hosts': [{
        'name': 'sit-k3s-v6',
        'host_vars': {
            'k3s_identity_environment': 'sit',
            'k3s_identity_cluster': 'sit-k3s-v6',
            'k3s_identity_api_server': 'https://127.0.0.1:6443',
            'k3s_identity_tls_dir': '/var/lib/rancher/k3s/server/tls',
            'k3s_platform_install_dir': '/var/lib/rancher/k3s',
            'k3s_identity_vault_writer_role': 'github-actions-platform-ops-toolkit-sit-k3s-v6-identity-writer',
            'k3s_identity_checks': {'root': [], 'operator': [], 'auditor': []},
            'k3s_identity_rbac_kustomizations': [{'name': 'cluster-identities-sit', 'namespace': 'flux-system'}],
            'service_domains': ['ignored.example'],
        },
    }],
}
PROBE = """
- hosts: k3s
  gather_facts: false
  tasks:
    - ansible.builtin.include_role: {name: vhosts/k3s_identity, tasks_from: from_declaration.yml}
    - ansible.builtin.copy:
        dest: "{{ out }}"
        content: "{{ {'cluster': k3s_identity_cluster, 'install': k3s_platform_install_dir, 'rev': k3s_identity_rbac_revision, 'domains': service_domains | default('unset')} | to_json }}"
      delegate_to: localhost
"""


class StaticContract(unittest.TestCase):
    def test_playbook_reads_declaration_before_role(self):
        play = yaml.safe_load((ROOT / 'k3s_identity_bootstrap.yml').read_text())[0]
        self.assertEqual(play['pre_tasks'][0]['ansible.builtin.include_role']['tasks_from'], 'from_declaration.yml')
        self.assertEqual(play['tasks'][0]['ansible.builtin.include_role']['name'], 'vhosts/k3s_identity')


@unittest.skipUnless(shutil.which('ansible-playbook'), 'ansible-playbook not installed')
class Binding(unittest.TestCase):
    def run_probe(self, host, declaration=DECLARATION, sha=SHA):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'decl.yaml').write_text(yaml.safe_dump(declaration))
            (tmp / 'probe.yml').write_text(PROBE)
            (tmp / 'inv.ini').write_text(f'[k3s]\n{host} ansible_connection=local\n')
            out = tmp / 'out.json'
            env = dict(os.environ, ANSIBLE_ROLES_PATH=str(ROOT / 'roles'))
            res = subprocess.run(['ansible-playbook', '-i', str(tmp / 'inv.ini'), str(tmp / 'probe.yml'),
                                  '-e', f'k3s_identity_manifest={tmp / "decl.yaml"}', '-e', f'k3s_identity_gitops_sha={sha}',
                                  '-e', f'out={out}'], capture_output=True, text=True, env=env, cwd=ROOT)
            return res, (json.loads(out.read_text()) if out.exists() else None)

    def test_binds_only_prefixed_keys_and_revision(self):
        res, out = self.run_probe('sit-k3s-v6')
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(out, {'cluster': 'sit-k3s-v6', 'install': '/var/lib/rancher/k3s', 'rev': SHA, 'domains': 'unset'})

    def test_undeclared_host_refused(self):
        res, out = self.run_probe('other-host')
        self.assertNotEqual(res.returncode, 0)
        self.assertIsNone(out)

    def test_bad_revision_refused(self):
        res, out = self.run_probe('sit-k3s-v6', sha='main')
        self.assertNotEqual(res.returncode, 0)

    def test_environment_mismatch_refused(self):
        bad = yaml.safe_load(yaml.safe_dump(DECLARATION))
        bad['global']['environment'] = 'prod'
        res, out = self.run_probe('sit-k3s-v6', declaration=bad)
        self.assertNotEqual(res.returncode, 0)


if __name__ == '__main__':
    unittest.main()
