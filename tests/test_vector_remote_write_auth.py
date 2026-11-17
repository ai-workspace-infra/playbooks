"""Contract tests for Vector remote-write authentication in pull mode.

The control plane injects VECTOR_AUTH_USER/VECTOR_AUTH_PASSWORD as environment
variables. ansible-pull has no such environment, so a pull run used to render
sinks whose auth block was simply absent — the node looked healthy while every
metric and log was rejected by the ingest gateway. These tests pin the
fail-closed behaviour and the three rendered forms.
"""

import json
import re
import tomllib
import unittest
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / 'roles/vhosts/vector-agent/templates'


class RemoteWriteAuthTests(unittest.TestCase):
    def render(self, mode, **overrides):
        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
        env.filters.update(
            bool=lambda v: str(v).lower() in ('true', '1', 'yes'),
            to_json=json.dumps,
            regex_replace=lambda s, a, b: re.sub(a, b, s),
            urlencode=lambda v: quote(str(v), safe=''),
        )
        context = dict(
            groups={},
            inventory_hostname='edge.example.test',
            vector_observability_node_identity='node.example.test',
            vector_observability_environment='uat',
            vector_observability_provider='test',
            vector_observability_service_domain='edge.example.test',
            vector_billing_ingest_enabled=False,
            blackbox_ssl_targets=[],
            blackbox_exporter_role_defaults={'blackbox_ssl_targets': []},
            vector_system_journald_enabled=False,
            vector_xhttp_monitoring_enabled=False,
            vector_auth_mode=mode,
            vector_auth_external_file='/run/vector/remote-write-auth.json',
            vector_auth_user='observability',
            vector_auth_password='placeholder',
        )
        context.update(overrides)
        rendered = env.from_string(
            (TEMPLATE_DIR / 'vector.toml.j2').read_text()
        ).render(**context)
        return tomllib.loads(rendered)

    def test_static_mode_renders_literal_credentials(self):
        config = self.render('static')
        auth = config['sinks']['prometheus_remote']['auth']
        self.assertEqual(auth['user'], 'observability')
        self.assertEqual(auth['password'], 'placeholder')

    def test_static_file_mode_uses_environment_indirection(self):
        config = self.render('static-file')
        auth = config['sinks']['prometheus_remote']['auth']
        # A literal secret must never land in vector.toml; the value comes from
        # the EnvironmentFile systemd injects.
        self.assertEqual(auth['user'], '${VECTOR_AUTH_USER}')
        self.assertEqual(auth['password'], '${VECTOR_AUTH_PASSWORD}')

    def test_vault_mode_uses_exec_secret_provider(self):
        config = self.render('vault')
        self.assertEqual(
            config['secret']['agent_proxy_vault']['command'],
            [
                '/usr/local/libexec/agent-proxy-vector-secret-provider',
                '/run/vector/remote-write-auth.json',
            ],
        )
        auth = config['sinks']['prometheus_remote']['auth']
        self.assertEqual(auth['user'], 'SECRET[agent_proxy_vault.user]')

    def test_none_mode_renders_no_auth_block(self):
        # 'none' only reaches the template when the caller bypassed the role's
        # assertion; the template must not invent a credential.
        config = self.render('none')
        self.assertNotIn('auth', config['sinks']['prometheus_remote'])

    def test_http_probes_are_hourly_and_off_by_default(self):
        config = self.render('static', vector_blackbox_probe_enabled=False)
        self.assertFalse(
            [name for name in config['sources'] if name.startswith('blackbox_')],
            'edge hosts must not render global SSL probes by default',
        )

    def test_hourly_probe_interval_when_enabled(self):
        targets = [{'name': 'www.svc.plus', 'url': 'https://www.svc.plus'}]
        config = self.render(
            'static',
            vector_blackbox_probe_enabled=True,
            blackbox_ssl_targets=targets,
            blackbox_probe_interval_secs=3600,
        )
        source = config['sources']['blackbox_www_svc_plus_ssl']
        self.assertEqual(source['scrape_interval_secs'], 3600)


if __name__ == '__main__':
    unittest.main()
