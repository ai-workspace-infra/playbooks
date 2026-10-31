"""Exercise role composition and fail-before-mutation contracts without a cluster."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / 'roles/vhosts'


class RoleContracts(unittest.TestCase):
    def run_role(self, role, *, phase='main.yml', variables=None, list_tasks=False):
        # Only list tasks or execute the explicitly read-only preflight phase.
        self.assertTrue(list_tasks or phase == 'preflight.yml')
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'test.yml'
            play.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'gather_facts': False,
                'vars': variables or {},
                'tasks': [{'ansible.builtin.import_role': {
                    'name': f'vhosts/{role}', 'tasks_from': phase}}],
            }]))
            config = Path(directory) / 'ansible.cfg'
            config.write_text('[defaults]\nstdout_callback=default\n')
            return subprocess.run(
                [sys.executable, '-m', 'ansible.cli.playbook', '-i', 'localhost,',
                 '-c', 'local', str(play)] + (['--list-tasks'] if list_tasks else []),
                cwd=ROOT, env={**os.environ, 'ANSIBLE_CONFIG': str(config),
                               'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')},
                capture_output=True, text=True, timeout=45)

    def test_coordinator_has_only_role_calls_and_keeps_dependency_order(self):
        tasks = yaml.safe_load((ROLES / 'k3s_platform_bootstrap/tasks/main.yml').read_text())
        for task in tasks:
            self.assertEqual(set(task) - {'name', 'when'}, {'ansible.builtin.import_role'})
        listing = self.run_role('k3s_platform_bootstrap', list_tasks=True)
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        ordered = ['Require explicit identity delivery contract',
                   'Require the declared node identity before K3s installation',
                   'Run k3s installer script', 'Wait for Kubernetes API readiness',
                   'Install helm', 'Install flux controllers',
                   'Apply source and root then verify Flux handover',
                   'Generate verify and archive independent client credentials on master']
        positions = [listing.stdout.index(name) for name in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_component_entrypoints_resolve_their_own_tasks(self):
        for role in ('k3s-cluster-server', 'helm_cli', 'flux_controllers',
                     'flux_gitops', 'k3s_identity'):
            with self.subTest(role=role):
                listing = self.run_role(role, list_tasks=True)
                self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)

    def test_independent_flux_preflight_accepts_names_unrelated_to_hostname(self):
        result = self.run_role('flux_gitops', phase='preflight.yml', variables={
            'k3s_platform_git_url': 'https://example.invalid/team/gitops.git',
            'k3s_platform_git_branch': 'main',
            'k3s_platform_flux_root_path': './environments/selected-batch',
            'k3s_platform_flux_root_name': 'environment-root',
            'k3s_platform_flux_source_name': 'management-source',
            'k3s_platform_node_name': 'independent-hostname',
            'k3s_platform_git_auth_mode': 'public',
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_flux_declaration_fails_before_host_changes(self):
        result = self.run_role('flux_gitops', phase='preflight.yml', variables={
            'k3s_platform_git_url': '', 'k3s_platform_flux_root_path': '',
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Validate bootstrap inputs', result.stdout)
        self.assertNotIn('Render flux bootstrap env file', result.stdout)

    def test_wrong_os_hostname_is_rejected_without_rewriting_it(self):
        result = self.run_role('k3s-cluster-server', phase='preflight.yml', variables={
            'k3s_platform_node_name': 'intentionally-wrong-' + socket.gethostname(),
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Run vhosts/common', result.stdout)
        self.assertNotIn('Install bootstrap dependencies', result.stdout)

    def test_unimplemented_lifecycle_action_cannot_succeed_as_empty_task(self):
        result = self.run_role('k3s-cluster-server', phase='preflight.yml',
                               variables={'action': 'upgrade'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('initializes the control plane only', result.stdout)
        self.assertNotIn('Verify the OS hostname', result.stdout)

    def test_legacy_nondefault_cni_fails_before_installation(self):
        result = self.run_role('k3s-cluster-server', phase='preflight.yml',
                               variables={'cni': 'kubeovn'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('default CNI', result.stdout)
        self.assertNotIn('Verify the OS hostname', result.stdout)

    def test_retired_installer_is_not_a_second_execution_path(self):
        self.assertFalse((ROLES / 'k3s-cluster').exists())
        self.assertFalse((ROOT / 'scripts/k3s-cluster/init_k3s_cluster_server_role.sh').exists())
        for entry in ('k3s-cluster.yaml', 'setup-k3s-node.yaml', 'init_k3s_cluster_server'):
            play = yaml.safe_load((ROOT / entry).read_text())[0]
            self.assertEqual(play['hosts'], 'k3s')
            self.assertEqual(play['roles'], ['vhosts/k3s-cluster-server'])


if __name__ == '__main__':
    unittest.main()
