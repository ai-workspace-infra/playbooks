#!/usr/bin/env python3
"""Render real Ansible templates locally. Does not run Docker or deploy."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/docker/grafana'


class GrafanaRuntimeTest(unittest.TestCase):
    def render(self, enabled=True, changes=None):
        with tempfile.TemporaryDirectory(prefix='grafana-oidc-test-') as directory:
            work = Path(directory)
            variables = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
            variables.update(grafana_workspace=str(work), grafana_env_file=str(work / 'grafana.env'),
                grafana_oidc_enabled=enabled, grafana_oidc_issuer='https://idp.example.test/',
                grafana_protocol='https', grafana_admin_password='FAKE_ADMIN_SECRET',
                grafana_oidc_client_id='test-client', grafana_oidc_client_secret='FAKE_OIDC_SECRET',
                grafana_oidc_role_attribute_path="contains(roles[*], 'admin') && 'Admin' || 'Viewer'")
            variables.update(changes or {})
            source_tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
            validation = next(t for t in source_tasks if t['name'] == 'Validate Grafana OIDC settings')
            environment = next(t for t in source_tasks if t['name'] == 'Template Grafana runtime environment')
            environment = json.loads(json.dumps(environment))
            environment['become'] = False
            environment['ansible.builtin.template']['src'] = str(ROLE / 'templates/grafana.env.j2')
            environment['ansible.builtin.template'].pop('owner')
            environment['ansible.builtin.template'].pop('group')
            compose = dict(name='Render Compose only', **{'ansible.builtin.template': dict(
                src=str(ROLE / 'templates/docker-compose.yaml.j2'), dest=str(work / 'compose.yaml'))})
            playbook = work / 'test.yml'
            playbook.write_text(yaml.safe_dump([dict(hosts='localhost', gather_facts=False,
                vars=variables, tasks=[validation, environment, compose])], sort_keys=False))
            env = dict(os.environ, ANSIBLE_NOCOLOR='1', ANSIBLE_LOCAL_TEMP=str(work / 'ansible-tmp'),
                       ANSIBLE_CONFIG=str(work / 'ansible.cfg'))
            (work / 'ansible.cfg').write_text('[defaults]\nstdout_callback=default\n')
            proc = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local',
                str(playbook), '--diff'], cwd=work, env=env, text=True, capture_output=True)
            self.assertNotIn('FAKE_OIDC_SECRET', proc.stdout + proc.stderr)
            self.assertNotIn('FAKE_ADMIN_SECRET', proc.stdout + proc.stderr)
            runtime = work / 'grafana.env'
            content = runtime.read_text() if runtime.exists() else ''
            mode = runtime.stat().st_mode & 0o777 if runtime.exists() else None
            composed = yaml.safe_load((work / 'compose.yaml').read_text()) if (work / 'compose.yaml').exists() else {}
            return proc, content, mode, composed

    def test_enabled_real_template(self):
        proc, content, mode, compose = self.render()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(mode, 0o600)
        entries = dict(line.split('=', 1) for line in content.splitlines() if '=' in line)
        self.assertEqual(entries['GF_AUTH_GENERIC_OAUTH_AUTH_URL'], 'https://idp.example.test/oauth/v2/authorize')
        self.assertEqual(entries['GF_AUTH_GENERIC_OAUTH_TOKEN_URL'], 'https://idp.example.test/oauth/v2/token')
        self.assertEqual(entries['GF_AUTH_GENERIC_OAUTH_API_URL'], 'https://idp.example.test/oidc/v1/userinfo')
        for key in ['USE_PKCE', 'ROLE_ATTRIBUTE_STRICT']:
            self.assertEqual(entries['GF_AUTH_GENERIC_OAUTH_' + key], 'true')
        self.assertEqual(entries['GF_AUTH_GENERIC_OAUTH_ALLOW_SIGN_UP'], 'false')
        self.assertEqual(entries['GF_SERVER_ROOT_URL'], 'https://grafana.svc.plus/')
        self.assertIn('grafana.env', str(compose['services']['grafana']['env_file']))
        self.assertNotIn('FAKE_OIDC_SECRET', str(compose))

    def test_disabled(self):
        proc, content, mode, _ = self.render(False, {'grafana_oidc_client_secret': ''})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('GF_AUTH_GENERIC_OAUTH_ENABLED=false', content)
        self.assertNotIn('GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=', content)
        self.assertEqual(mode, 0o600)

    def test_reject_incomplete_or_insecure_settings_before_render(self):
        changes = [{key: ''} for key in ['grafana_oidc_issuer', 'grafana_oidc_client_id',
                   'grafana_oidc_client_secret', 'grafana_oidc_role_attribute_path']]
        changes += [{'grafana_oidc_issuer': 'http://idp.example.test'}, {'grafana_protocol': 'http'}]
        for change in changes:
            with self.subTest(change=change):
                proc, content, mode, _ = self.render(changes=change)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(content, '')
                self.assertIsNone(mode)


if __name__ == '__main__':
    unittest.main(verbosity=2)
