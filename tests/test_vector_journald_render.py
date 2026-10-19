import json
from pathlib import Path
import re
import tomllib
import unittest
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


class JournalRenderTests(unittest.TestCase):
    def render(self, enabled, xhttp=False):
        env = Environment(loader=FileSystemLoader(ROOT / 'roles/vhosts/vector-agent/templates'))
        env.filters.update(bool=lambda v: str(v).lower() in ('true', '1', 'yes'),
                           to_json=json.dumps,
                           regex_replace=lambda s, a, b: re.sub(a, b, s),
                           urlencode=lambda v: quote(str(v), safe=''))
        template = env.from_string((ROOT / 'roles/vhosts/vector-agent/templates/vector.toml.j2').read_text())
        result = template.render(groups={}, inventory_hostname='edge.example.test',
                                 vector_observability_environment='uat',
                                 vector_observability_provider='test',
                                 vector_observability_service_domain='edge.example.test',
                                 vector_billing_ingest_enabled=False,
                                 blackbox_ssl_targets=[],
                                 blackbox_exporter_role_defaults={"blackbox_ssl_targets": []},
                                 vector_system_journald_enabled=enabled,
                                 vector_xhttp_monitoring_enabled=xhttp,
                                 vector_system_journald_units=['xconnect-edge-agent.service', 'xray.service'])
        return tomllib.loads(result)

    def test_enabled_journal_reaches_existing_labeled_sink(self):
        config = self.render(True)
        self.assertEqual(config['sources']['system_journal']['type'], 'journald')
        self.assertEqual(config['sources']['system_journal']['include_units'],
                         ['xconnect-edge-agent.service', 'xray.service'])
        self.assertIn('system_journal', config['transforms']['process_logs']['inputs'])
        self.assertIn('process_logs', config['sinks']['victorialogs']['inputs'])
        self.assertIn('.instance = "edge.example.test"', config['transforms']['process_logs']['source'])

    def test_existing_file_only_path_is_preserved(self):
        config = self.render(False)
        self.assertNotIn('system_journal', config['sources'])
        self.assertEqual(config['transforms']['process_logs']['inputs'], ['system_logs'])

    def test_xhttp_metrics_use_existing_sink_and_one_journal_reader(self):
        config = self.render(True, xhttp=True)
        self.assertNotIn('system_journal', config['sources'])
        self.assertIn('init.scope', config['sources']['xhttp_journal']['include_units'])
        self.assertTrue(config['sources']['xhttp_journal']['since_now'])
        self.assertIn('xhttp_journal', config['transforms']['process_logs']['inputs'])
        self.assertEqual(config['transforms']['xhttp_events']['inputs'], ['xhttp_journal'])
        for source in ['xhttp_caddy_labels', 'xhttp_event_metrics']:
            self.assertIn(source, config['sinks']['prometheus_remote']['inputs'])
        metric = config['transforms']['xhttp_event_metrics']['metrics'][0]
        self.assertEqual(metric['tags']['instance'], '{{ instance }}')
        self.assertEqual(set(metric['tags']), {'instance', 'environment', 'event'})

    def test_dashboard_is_valid_and_uses_bounded_event_series(self):
        dashboard = json.loads((ROOT / 'roles/docker/observability-server/files/xconnect-comprehensive.json').read_text())
        self.assertEqual(dashboard['uid'], 'xconnect-comprehensive')
        self.assertTrue(any('xhttp_events_total' in str(panel) for panel in dashboard['panels']))

    def test_snapshot_only_relay_gets_shared_xray_collection(self):
        env = Environment(loader=FileSystemLoader(ROOT / 'roles/vhosts/vector-agent/templates'), trim_blocks=True)
        env.filters.update(bool=bool, to_json=json.dumps)
        rendered = env.get_template('xhttp-monitoring.toml.j2').render(
            vector_xhttp_monitoring_enabled=True, vector_xhttp_create_sink=True,
            vector_xhttp_add_xray_scrape=True, inventory_hostname='edge.example.test',
            vector_observability_environment='uat', vector_observability_provider='test',
            vector_observability_service_domain='edge.example.test',
            xhttp_monitoring_vault={'json': {'data': {'data': {'user': 'fixture', 'password': 'fixture'}}}})
        config = tomllib.loads(rendered)
        for transport, port in [('xhttp', 8080), ('tcp', 8081)]:
            self.assertEqual(config['sources'][f'xray_{transport}_metrics']['endpoints'],
                             [f'http://127.0.0.1:{port}/scrape'])
            self.assertIn(f'add_xray_{transport}_labels', config['sinks']['prometheus_remote']['inputs'])


if __name__ == '__main__':
    unittest.main()
