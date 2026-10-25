import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'roles/vhosts/vault_github_oidc/files/reconcile.py'
spec = importlib.util.spec_from_file_location('vault_github_oidc', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def request():
    return {
        'address': 'https://vault.example.test', 'token': 'fixture',
        'policies': {'github-actions-demo': 'fixture'},
        'roles': {'github-actions-demo-prod': {
            'role_type': 'jwt', 'user_claim': 'sub', 'bound_audiences': ['vault'],
            'bound_claims': {
                'repository': 'example/demo',
                'job_workflow_ref': 'example/demo/.github/workflows/verify.yml@*',
                'ref': ['refs/tags/v*'],
            },
            'token_no_default_policy': True, 'token_policies': ['github-actions-demo'],
            'token_type': 'batch', 'token_ttl': '1h', 'token_max_ttl': '1h',
        }},
    }


class WorkloadIdentityGuards(unittest.TestCase):
    def test_production_rejects_main_and_legacy_generation_tags(self):
        for ref in ['refs/heads/main', 'refs/tags/prod-v2-*']:
            with self.subTest(ref=ref):
                config = request()
                config['roles']['github-actions-demo-prod']['bound_claims']['ref'] = [ref]
                with self.assertRaises(ValueError):
                    module.validate(config)

    def test_rejects_wildcard_repository(self):
        config = request()
        config['roles']['github-actions-demo-prod']['bound_claims']['repository'] = 'example/*'
        with self.assertRaises(ValueError):
            module.validate(config)

    def test_rejects_wildcard_workflow_name(self):
        config = request()
        config['roles']['github-actions-demo-prod']['bound_claims']['job_workflow_ref'] = 'example/demo/.github/workflows/*@*'
        with self.assertRaises(ValueError):
            module.validate(config)

    def test_rejects_unreferenced_policy_mutation(self):
        config = request()
        config['policies']['github-actions-unrelated'] = 'fixture'
        with self.assertRaises(ValueError):
            module.validate(config)

    def test_rejects_default_policy_grant(self):
        config = request()
        config['roles']['github-actions-demo-prod']['token_no_default_policy'] = False
        with self.assertRaises(ValueError):
            module.validate(config)

    def test_accepts_explicit_stable_release_contract(self):
        module.validate(request())


if __name__ == '__main__':
    unittest.main()
