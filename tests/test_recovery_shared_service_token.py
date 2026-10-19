"""Exercise real Ansible Vault-read guards without accessing a live Vault."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import jinja2

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/web_saas_generation_recovery/tasks/service-token.yml'


class SharedTokenTests(unittest.TestCase):
    def run_read(self, token, path='kv/prod/accounts/runtime'):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = tmp / 'compose/web-saas/runtime'
            config.mkdir(parents=True)
            (config / 'prod.yaml').write_text(json.dumps({'internal_service_token': {
                'vault_path': path, 'key': 'INTERNAL_SERVICE_TOKEN'}}))
            fake = tmp / 'vault'
            fake.write_text('#!/bin/sh\nprintf "%s" "$TEST_SERVICE_TOKEN"\n')
            fake.chmod(0o700)
            play = tmp / 'play.yaml'
            play.write_text(json.dumps([{'hosts': 'localhost', 'gather_facts': False,
                'vars': {'web_saas_recovery_gitops_checkout': str(tmp),
                         'web_saas_recovery_phase': 'verify',
                         'web_saas_recovery_vault_addr': 'https://vault.invalid'},
                'tasks': [{'ansible.builtin.include_tasks': str(TASKS)}]}]))
            return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)],
                env=dict(os.environ, PATH=str(tmp)+os.pathsep+os.environ['PATH'],
                         TEST_SERVICE_TOKEN=token, ANSIBLE_NOCOLOR='1'),
                capture_output=True, text=True)

    def test_canonical_value_succeeds_without_disclosing_it(self):
        token = 'test-only-service-token-' + 'a'*40
        result = self.run_read(token)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_or_multiline_values_stop(self):
        for token in ['', 'a'*40+'\nINJECTED=bad']:
            result = self.run_read(token)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('INJECTED=bad', result.stdout + result.stderr)

    def test_generation_scoped_path_is_rejected(self):
        self.assertNotEqual(self.run_read('a'*40, 'kv/prod-v3/web-saas').returncode, 0)

    def test_recovery_entropy_cannot_change_shared_token(self):
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters['hash'] = lambda value, algorithm: 'independent-secret'
        template = environment.from_string(
            (ROOT / 'roles/web_saas_generation_recovery/templates/secrets.env.j2').read_text())
        for seed in ['seed-one', 'seed-two']:
            rendered = template.render(recovery_seed=seed,
                recovery_internal_service_token='canonical-test-token',
                recovery={'target': {'domain_base': 'svc.plus'}})
            self.assertIn('INTERNAL_SERVICE_TOKEN=canonical-test-token\n', rendered)
        with self.assertRaises(jinja2.UndefinedError):
            template.render(recovery_seed='seed', recovery={'target': {'domain_base': 'svc.plus'}})


if __name__ == '__main__':
    unittest.main()
