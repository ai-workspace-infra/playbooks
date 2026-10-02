import json
from pathlib import Path
import re
import tomllib
import unittest
from urllib.parse import quote

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]


class JournalRenderTests(unittest.TestCase):
    def render(self, enabled):
        env = Environment()
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


if __name__ == '__main__':
    unittest.main()
