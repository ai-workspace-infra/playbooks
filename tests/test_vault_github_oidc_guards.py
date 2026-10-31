import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

MODULE = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_github_oidc/files/reconcile.py'
spec = importlib.util.spec_from_file_location('vault_github_oidc', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main_role():
    return {
        'role_type': 'jwt', 'user_claim': 'sub', 'bound_audiences': ['vault'],
        'bound_claims_type': 'glob', 'token_no_default_policy': True,
        'token_type': 'batch', 'token_ttl': '30m', 'token_max_ttl': '30m',
        'token_explicit_max_ttl': '30m',
        'bound_claims': {
            'repository': 'ai-workspace-infra/Platform-Orchestrator',
            'environment': 'prod', 'ref': ['refs/heads/main'],
            'ref_protected': 'true',
            'workflow_ref': 'ai-workspace-infra/Platform-Orchestrator/.github/workflows/selfhost-stage-deploy.yml@refs/heads/main',
        },
        'token_policies': ['github-actions-platform-ops-toolkit-prod-k3s-v6-bootstrap-reader'],
    }


def stable_release_role():
    return {
        'role_type': 'jwt', 'user_claim': 'sub', 'bound_audiences': ['vault'],
        'bound_claims': {
            'repository': 'example/demo',
            'job_workflow_ref': 'example/demo/.github/workflows/verify.yml@*',
            'ref': ['refs/tags/v*'],
        },
        'token_no_default_policy': True,
        'token_policies': ['github-actions-demo'],
        'token_type': 'batch', 'token_ttl': '1h', 'token_max_ttl': '1h',
    }


def policy(paths=None, capabilities=None):
    paths = paths or ['kv/data/prod/security/root']
    capabilities = capabilities or ['read']
    return {
        'apiVersion': 'security.gitops/v1alpha1', 'kind': 'VaultACLPolicy',
        'metadata': {'name': 'github-actions-platform-ops-toolkit-prod-k3s-v6-bootstrap-reader'},
        'spec': {'paths': [{'path': path, 'capabilities': capabilities} for path in paths]},
    }


def role_document(role, role_name):
    return {
        'apiVersion': 'security.gitops/v1alpha1', 'kind': 'VaultJWTAuthRole',
        'metadata': {
            'name': role_name,
            'description': 'test role',
        },
        'spec': role,
    }


class WorkloadIdentityGuards(unittest.TestCase):
    def test_admin_token_is_read_from_runtime_environment_not_request_args(self):
        tasks = yaml.safe_load((MODULE.parents[1] / 'tasks/main.yml').read_text())
        validate = tasks[0]
        reconcile = tasks[1]
        self.assertIn("'token' not in vault_github_oidc_request", validate['ansible.builtin.assert']['that'])
        self.assertIn("lookup('env', 'VAULT_TOKEN')", reconcile['ansible.builtin.command']['stdin'])
        self.assertNotIn('vault_github_oidc_request.token', reconcile['ansible.builtin.command']['stdin'])

    def config(self, role, policy_doc=None, role_name=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        roles = root / 'security/vault/roles'
        policies = root / 'security/vault/policies'
        roles.mkdir(parents=True)
        policies.mkdir(parents=True)
        role_name = role_name or role['token_policies'][0]
        policy_name = role['token_policies'][0]
        policy_doc = policy_doc or policy()
        role_doc = role_document(role, role_name)
        policy_doc['metadata']['name'] = policy_name
        (roles / f'{role_name}.yaml').write_text(yaml.safe_dump(role_doc, sort_keys=False))
        (policies / f'{policy_name}.yaml').write_text(yaml.safe_dump(policy_doc, sort_keys=False))
        subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(root), 'remote', 'add', 'origin', 'https://github.com/ai-workspace-infra/gitops.git'], check=True)
        subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Contract Test'], check=True)
        subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'contract@example.invalid'], check=True)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
        sha = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True, text=True, capture_output=True).stdout.strip()
        return {'gitops': {'root': str(root), 'sha': sha, 'role_name': role_name}}

    def test_accepts_protected_prod_main_exact_workflow_and_read_only_paths(self):
        config = self.config(main_role(), policy([
            'kv/data/prod/platform/prod-k3s-v6/ssh',
            'kv/data/prod/security/root',
            'kv/data/prod/security/operator',
            'kv/data/prod/security/auditor',
        ]))
        roles, policies, sha = module.resolve(config)
        self.assertEqual(set(roles), {config['gitops']['role_name']})
        self.assertEqual(set(policies), set(roles[config['gitops']['role_name']]['token_policies']))
        self.assertEqual(sha, config['gitops']['sha'])

    def test_rejects_prod_main_without_protected_environment(self):
        role = main_role()
        role['bound_claims']['ref_protected'] = 'false'
        with self.assertRaises(ValueError):
            module.resolve(self.config(role))

    def test_rejects_prod_main_workflow_wildcard(self):
        role = main_role()
        role['bound_claims']['workflow_ref'] = 'ai-workspace-infra/Platform-Orchestrator/.github/workflows/*@refs/heads/main'
        with self.assertRaises(ValueError):
            module.resolve(self.config(role))

    def test_rejects_prod_main_writer_policy(self):
        with self.assertRaises(ValueError):
            module.resolve(self.config(main_role(), policy(capabilities=['read', 'create'])))

    def test_rejects_prod_main_wildcard_path(self):
        with self.assertRaises(ValueError):
            module.resolve(self.config(main_role(), policy(['kv/data/prod/security/*'])))

    def test_rejects_prod_main_ttl_over_thirty_minutes(self):
        role = main_role()
        role['token_ttl'] = '1h'
        role['token_max_ttl'] = '1h'
        role['token_explicit_max_ttl'] = '1h'
        with self.assertRaises(ValueError):
            module.resolve(self.config(role))

    def test_accepts_existing_stable_release_contract(self):
        role = stable_release_role()
        policy_doc = {
            'apiVersion': 'security.gitops/v1alpha1', 'kind': 'VaultACLPolicy',
            'metadata': {'name': 'github-actions-demo'},
            'spec': {'paths': [{'path': 'kv/data/prod/example', 'capabilities': ['read']}]},
        }
        config = self.config(role, policy_doc, 'github-actions-demo-prod')
        resolved_roles, _, _ = module.resolve(config)
        self.assertEqual(set(resolved_roles), {'github-actions-demo-prod'})

    def test_rejects_versioned_generation_refs_for_prod(self):
        role = stable_release_role()
        role['bound_claims']['ref'] = ['refs/tags/prod-v2-*']
        with self.assertRaises(ValueError):
            module.resolve(self.config(role, role_name='github-actions-demo-prod'))

    def test_rejects_gitops_sha_mismatch(self):
        config = self.config(main_role())
        config['gitops']['sha'] = '0' * 40
        with self.assertRaises(ValueError):
            module.resolve(config)

    def test_rejects_dirty_gitops_checkout(self):
        config = self.config(main_role())
        path = Path(config['gitops']['root']) / 'security/vault/roles' / f"{config['gitops']['role_name']}.yaml"
        path.write_text(path.read_text() + '\n# unreviewed\n')
        with self.assertRaises(ValueError):
            module.resolve(config)

    def test_rejects_legacy_copied_role_maps(self):
        with self.assertRaises(ValueError):
            module.resolve({'roles': {'github-actions-demo-prod': stable_release_role()}, 'policies': {'github-actions-demo': 'path "x" {}'}})


if __name__ == '__main__':
    unittest.main()
