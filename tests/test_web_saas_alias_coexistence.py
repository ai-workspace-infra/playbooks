"""Render real Caddy templates locally; no network or target access."""
from pathlib import Path
import unittest
import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]


class AliasCoexistenceTests(unittest.TestCase):
    def render(self, restored):
        defaults = yaml.safe_load((ROOT / 'roles/vhosts/web_saas_host_config/defaults/main.yml').read_text())
        values = {key: '' if isinstance(value, str) else value for key, value in defaults.items()}
        values.update(web_saas_host_config_candidate_mode=True,
                      web_saas_host_config_use_restored_tls=restored,
                      web_saas_host_config_acme_dns_enabled=False,
                      web_saas_host_config_billing_allowed_cidrs='192.0.2.1/32')
        for service in ('console', 'accounts', 'billing'):
            prefix = 'web_saas_host_config_' + service
            values[prefix + '_domain'] = service + '-selfhost-prod-v5.example.invalid'
            values[prefix + '_public_domain'] = service + '.example.invalid'
            # Include duplicates to prove a single matcher/site entry per name.
            values[prefix + '_aliases'] = [service + '-prod-v5.example.invalid',
                                           service + '.example.invalid',
                                           service + '-prod-v5.example.invalid']
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters['bool'] = bool
        template = (ROOT / 'roles/vhosts/web_saas_host_config/templates/Caddyfile.j2').read_text()
        return environment.from_string(template).render(**values)

    def test_restored_tls_serves_all_names_without_duplicate_matchers(self):
        rendered = self.render(True)
        for service in ('console', 'accounts'):
            matcher = next(line for line in rendered.splitlines() if '@' + service + ' host ' in line)
            for name in (service + '-selfhost-prod-v5.example.invalid',
                         service + '.example.invalid', service + '-prod-v5.example.invalid'):
                self.assertEqual(matcher.split().count(name), 1)
        billing = next(line for line in rendered.splitlines() if '@public_billing host ' in line)
        self.assertEqual(billing.split().count('billing.example.invalid'), 1)
        self.assertEqual(billing.split().count('billing-prod-v5.example.invalid'), 1)
        self.assertIn('reverse_proxy accounts:8080', rendered)
        self.assertIn('reverse_proxy console:3000', rendered)

    def test_http_tls_sites_preserve_generation_and_canonical_aliases(self):
        rendered = self.render(False)
        for service in ('console', 'accounts', 'billing'):
            site = next(line for line in rendered.splitlines() if line.startswith('https://' + service + '-selfhost'))
            self.assertEqual(site.count('https://' + service + '-prod-v5.example.invalid'), 1)
            self.assertEqual(site.count('https://' + service + '.example.invalid'), 1)


if __name__ == '__main__':
    unittest.main()
