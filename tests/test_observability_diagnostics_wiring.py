from pathlib import Path
import base64
import re
import unittest
import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticsWiringTests(unittest.TestCase):
    def test_shared_plugin_mount_remains_writable_for_grafana(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/observability-server/tasks/xconnect_diagnostics.yml').read_text())
        directory = tasks[0]
        options = directory['ansible.builtin.file']
        self.assertEqual(options['path'], '{{ observability_dir }}/grafana/plugins')
        self.assertEqual(options['owner'], '472')
        self.assertEqual(options['group'], '472')
        self.assertEqual(options['mode'], '0755')
        self.assertNotIn('recurse', options)
        self.assertNotIn('when', directory)
        provisioning = next(t for t in tasks if t['name'].startswith('Provision XConnect diagnostics Grafana app'))['ansible.builtin.template']
        self.assertEqual(provisioning['owner'], '472')
        self.assertEqual(provisioning['mode'], '0600')

    def test_credentials_reuse_monitoring_source_without_reusing_agent_password(self):
        defaults = yaml.safe_load((ROOT / 'roles/docker/observability-server/defaults/main.yml').read_text())
        self.assertEqual(defaults['observability_xconnect_diagnostics_vault_path'], 'kv/data/CICD/observability')
        tasks = yaml.safe_load((ROOT / 'roles/docker/observability-server/tasks/diagnostics_credentials.yml').read_text())
        writer = next(t for t in tasks if t['name'].startswith('Preserve monitoring credentials'))
        self.assertIn('cas', writer['ansible.builtin.uri']['body']['options'])
        self.assertIn('combine', writer['ansible.builtin.uri']['body']['data'])
        resolver = next(t for t in tasks if t['name'].startswith('Resolve XConnect'))
        self.assertIn('IDENTITY_LOOKUP_TOKEN', str(resolver))
        self.assertNotIn("get('password'", str(resolver))
        self.assertTrue(writer['no_log'])

    def test_resolver_is_probed_before_plugin_install(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/observability-server/tasks/xconnect_diagnostics.yml').read_text())
        names = [t['name'] for t in tasks]
        self.assertLess(names.index('Verify scoped Accounts resolver before publishing the app'), names.index('Download pinned XConnect diagnostics Grafana plugin release'))
        probe = tasks[names.index('Verify scoped Accounts resolver before publishing the app')]
        self.assertEqual(probe['ansible.builtin.uri']['status_code'], 400)

    def test_smtp_rotation_preserves_only_the_scoped_field(self):
        env = Environment(trim_blocks=True)
        env.filters['b64decode'] = lambda x: base64.b64decode(x).decode()
        env.filters['regex_search'] = lambda x, p: (m.group(0) if (m := re.search(p, x)) else None)
        template = env.from_string((ROOT / 'templates/prod-v2/accounts-smtp.env.j2').read_text())
        line = 'XCONNECT_OBSERVABILITY_SEARCH_TOKEN=' + 't' * 48
        existing = base64.b64encode(('SMTP_PASSWORD=old\n' + line + '\nUNRELATED=ignored\n').encode()).decode()
        rendered = template.render(prod_services_smtp_env={'SMTP_HOST': 'smtp.example.test'}, accounts_scoped_runtime_existing={'content': existing})
        self.assertIn(line, rendered)
        self.assertNotIn('old', rendered)
        self.assertNotIn('UNRELATED', rendered)
        fresh = template.render(prod_services_smtp_env={'SMTP_HOST': 'smtp.example.test'})
        self.assertNotIn('XCONNECT_OBSERVABILITY_SEARCH_TOKEN=', fresh)

    def test_accounts_recreation_does_not_upgrade_other_services(self):
        text = (ROOT / 'roles/web_saas_prod_services/tasks/observability.yml').read_text()
        self.assertIn("'--no-deps', '--no-build', '--pull', 'never', 'accounts'", text)
        self.assertIn('services.accounts.image == diagnostics_accounts_runtime.Config.Image', text)
        self.assertIn('accounts-smtp.env', text)
