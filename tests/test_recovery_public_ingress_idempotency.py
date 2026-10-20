"""Historical SNI/ingestion regressions for fresh and restored generations."""
from pathlib import Path
import re
import unittest
import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]


class RecoveryPublicIngressIdempotencyTests(unittest.TestCase):
    def test_activation_requires_change_and_explicit_enablement(self):
        defaults = yaml.safe_load((ROOT / 'roles/web_saas_public_ingress/defaults/main.yml').read_text())
        tasks = yaml.safe_load((ROOT / 'roles/web_saas_public_ingress/tasks/main.yml').read_text())
        self.assertIs(defaults['web_saas_public_ingress_activate'], False)
        activate = next(task for task in tasks if task.get('name', '').startswith('Activate only changed'))
        self.assertIn('web_saas_public_ingress_activate | bool', activate['when'])
        self.assertTrue(any('public_ingress_published.changed' in item for item in activate['when']))

    def test_restore_and_fresh_deployment_share_ingress_owner(self):
        for file in ['roles/web_saas_generation_recovery/tasks/prepare.yml', 'roles/web_saas_generation_fresh/tasks/application.yml']:
            tasks = yaml.safe_load((ROOT / file).read_text())
            owners = [task['ansible.builtin.include_role']['name'] for task in tasks if 'ansible.builtin.include_role' in task]
            self.assertIn('web_saas_public_ingress', owners)
            self.assertNotIn('tls internal', (ROOT / file).read_text())
        self.assertFalse((ROOT / 'roles/web_saas_generation_recovery/templates/Caddyfile.j2').exists())
        smoke = (ROOT / 'roles/web_saas_generation_recovery/tasks/smoke.yml').read_text()
        self.assertNotIn('recovery-root-ca.crt', smoke)

    def test_all_sni_hosts_and_ingestion_survive_generation_render(self):
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters['bool'] = bool
        template = environment.from_string((ROOT / 'roles/web_saas_public_ingress/templates/public-sites.caddy.j2').read_text())
        contract = {'domains': {'console': 'console.svc.plus', 'accounts': 'accounts.svc.plus', 'billing': 'billing.svc.plus'},
                    'tls': {'directory': '/etc/xcontrol/tls/svc.plus'},
                    'brand_domains': {'root': 'xworktech.com', 'www': 'www.xworktech.com'},
                    'brand_tls': {'directory': '/etc/xcontrol/tls/xworktech.com'}}
        for generation in ['prod-v4', 'prod-v10']:
            for readonly in [False, True]:
                output = template.render(public_ingress=contract, web_saas_public_ingress_generation=generation,
                                         web_saas_public_ingress_recovery_read_only=readonly)
                for service in ['console', 'accounts', 'billing']:
                    self.assertIn(service + '-selfhost-' + generation + '.svc.plus', output)
                self.assertIn('redir https://xworktech.com{uri} 301', output)
                self.assertEqual('method POST\n        path /v1/ingest/snapshots' in output, not readonly)
                self.assertNotIn('tls internal', output)
                self.assertNotIn('header_up Authorization', output)
                self.assertEqual('respond @mutating 403' in output, readonly)

    def test_deployment_reference_accepts_only_fixed_sha_or_release_tag(self):
        tasks = yaml.safe_load((ROOT / 'roles/doco_cd_release_selection/tasks/main.yml').read_text())
        condition = next(value for value in tasks[0]['ansible.builtin.assert']['that'] if value.startswith('doco_release_reference'))
        expression = condition.split("match('", 1)[1].rsplit("')", 1)[0]
        self.assertTrue(re.fullmatch(expression, 'b5987fefc5e591e0fdba33c684ca83766a16ff37'))
        self.assertTrue(re.fullmatch(expression, 'refs/tags/prod-v4-fresh-2026.10.20-r1'))
        for floating in ['main', 'refs/heads/main', 'latest', 'b5987fe']:
            self.assertIsNone(re.fullmatch(expression, floating))


if __name__ == '__main__':
    unittest.main()
